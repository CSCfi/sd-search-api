"""Generic load service: store extracted documents and cache ontology preferred terms."""

import logging
from collections.abc import Iterator, Sequence
from datetime import datetime

from psycopg import AsyncCursor

from search_api.api.beacon.models import BeaconFilteringScope, BeaconFilteringTerm
from search_api.api.opensearch.document import build_document
from search_api.api.opensearch.models import (
    MAX_DOCUMENT_ID_BYTES,
    MAX_KEYWORD_BYTES,
    ExtractedDocument,
    OpenSearchFieldValue,
)
from search_api.api.scopes import validate_document_scope
from search_api.database.document import (
    delete_dropped_documents,
    get_modified_at,
    reset_synced_at_by_set,
    upsert_document,
)
from search_api.database.document_log import delete_document_logs, write_document_log
from search_api.database.models import StoredDocumentLog
from search_api.database.repository import get_cursor, get_transaction_cursor
from search_api.exceptions import SystemException, UserException
from search_api.services.fetch import DocumentSet
from search_api.services.ontology.term_cache import OntologyTermCache
from search_api.services.ontology.values import (
    cache_concept_terms,
    get_ontology_bindings,
    resolve_concepts,
)

logger = logging.getLogger(__name__)

# The field types indexed as keywords whose value is a string.
_KEYWORD_TYPES = ("keyword", "controlledValue")


def extraction_logs(doc: ExtractedDocument) -> Iterator[StoredDocumentLog]:
    """Return the log entries from document extraction."""
    for log in doc.logs:
        yield StoredDocumentLog(
            document_id=doc.id,
            field_id=log.field_id,
            severity=log.severity,
            message=log.message,
        )


def _is_too_long_keyword(value: OpenSearchFieldValue) -> bool:
    """Return true if OpenSearch would reject the value as too long a keyword."""
    return (
        value.field.type in _KEYWORD_TYPES
        and isinstance(value.value, str)
        and len(value.value.encode()) > MAX_KEYWORD_BYTES
    )


def drop_too_long_keywords(doc: ExtractedDocument) -> list[StoredDocumentLog]:
    """
    Drop the keyword values OpenSearch would reject as too long.

    :param doc: The document, changed in place.
    :return: An error log entry for each dropped value.
    """
    dropped: list[OpenSearchFieldValue] = []

    def kept(values: list[OpenSearchFieldValue]) -> list[OpenSearchFieldValue]:
        """Return the values to keep. The others are added to dropped."""
        result = []
        for value in values:
            if _is_too_long_keyword(value):
                dropped.append(value)
            else:
                result.append(value)
        return result

    doc.values = kept(doc.values)
    for group in doc.groups:
        group.values = kept(group.values)
    return [
        StoredDocumentLog(
            document_id=doc.id,
            field_id=value.field.id,
            severity="ERROR",
            message=f"Value is ignored: longer than {MAX_KEYWORD_BYTES} bytes.",
        )
        for value in dropped
    ]


class LoadService:
    """Store extracted documents and cache their ontology preferred terms."""

    def __init__(
        self,
        term_caches: dict[str, OntologyTermCache],
        filtering_terms: Sequence[BeaconFilteringTerm],
        filtering_scopes: Sequence[BeaconFilteringScope] = (),
        replace_concepts: bool = True,
    ) -> None:
        self._term_caches = term_caches
        self._ontology_bindings = get_ontology_bindings(filtering_terms, term_caches)
        self._filtering_scopes = filtering_scopes
        self._replace_concepts = replace_concepts
        self._initialised = False

    def validate_document(self, doc: ExtractedDocument) -> None:
        """Check an extracted document's id and scope against the deployment.

        This is where a deployment's extraction meets the generic service.

        :raises SystemException: if the id is too long for OpenSearch.
        :raises UserException: if the scope is not declared.
        """
        if len(doc.id.encode()) > MAX_DOCUMENT_ID_BYTES:
            raise SystemException(
                f"Document id is longer than {MAX_DOCUMENT_ID_BYTES} bytes."
            )
        try:
            validate_document_scope(doc.scope, self._filtering_scopes)
        except UserException as e:
            raise UserException(f"Document '{doc.id}': {e}") from e

    async def store_document(
        self,
        cur: AsyncCursor,
        doc: ExtractedDocument,
        modified_at: datetime,
        document_set_id: str | None,
    ) -> None:
        """Store one extracted document to the database.

        :param cur: The database cursor.
        :param doc: The extracted document.
        :param modified_at: When the document's document set was modified.
        :param document_set_id: The document's document set, or None for a deployment
            without document sets.
        :raises UserException: if the document does not match the deployment.
        """
        self.validate_document(doc)
        await upsert_document(
            cur, doc.id, build_document(doc), modified_at, document_set_id
        )

    async def _initialise(self) -> None:
        """Initialise in-memory caches and ontologies before documents are loaded."""
        if self._initialised:
            return

        for cache in self._term_caches.values():
            await cache.load()

        # The ontologies must be initialised before load to
        # resolve preferred terms for the terms cache.
        for ontology in {
            binding.ontology for binding in self._ontology_bindings.values()
        }:
            await ontology.init()

        self._initialised = True

    async def store_documents(
        self,
        docs_iter: Iterator[ExtractedDocument],
        modified_at: datetime,
        document_set_id: str | None,
    ) -> None:
        """
        Store extracted documents to the database.

        The documents are of one document set, and are stored with its modification
        date. A document whose stored date is not older is skipped. Preferred terms
        for ontology concepts are stored in the term cache for the concept's ontology.

        :param docs_iter: Iterator of extracted documents.
        :param modified_at: When the document set was modified.
        :param document_set_id: The document set's id, or None for a deployment
            without document sets.
        """
        await self._initialise()

        loaded = 0
        skipped = 0

        async with get_cursor() as cur:
            for doc in docs_iter:
                stored_modified_at = await get_modified_at(cur, doc.id)
                if stored_modified_at is not None and stored_modified_at >= modified_at:
                    logger.debug(
                        "Skipping document %s — not newer than stored.", doc.id
                    )
                    skipped += 1
                    continue

                # Write document's log rows and the document in one transaction.
                async with cur.connection.transaction():
                    # A document loaded again replaces its log rows.
                    await delete_document_logs(cur, [doc.id])

                    # Write log messages from document extraction.
                    for log in extraction_logs(doc):
                        await write_document_log(cur, log)

                    # Drop keyword values OpenSearch would reject because they are too long.
                    for log in drop_too_long_keywords(doc):
                        await write_document_log(cur, log)

                    # The document's ontology values are resolved to concept
                    # ids before the document is stored. Values that could not
                    # be resolved are logged.
                    await resolve_concepts(
                        cur, doc, self._ontology_bindings, self._replace_concepts
                    )
                    await cache_concept_terms(doc, self._ontology_bindings)
                    # The document must be stored last, because retry skips stored documents.
                    await self.store_document(cur, doc, modified_at, document_set_id)
                loaded += 1
                logger.debug("Loaded document %s.", doc.id)

        logger.info("Done — loaded %d, skipped %d document(s).", loaded, skipped)

    async def store_document_set(self, document_set: DocumentSet) -> None:
        """
        Store a document set's documents, and delete stored documents that are no longer part of it.

        :param document_set: Documents in the document set.
        """
        await self.store_documents(
            iter(document_set.documents), document_set.modified_at, document_set.id
        )
        await self._delete_dropped_documents(document_set)

    async def _delete_dropped_documents(self, document_set: DocumentSet) -> None:
        """
        Delete documents from the database that are no longer part of the current document set.

        The modified_at is a property of the document set but stored individually
        for each document. A stored document removed from the document set is
        deleted only if its modified_at precedes the current document set
        modified_at.

        The next sync deletes the documents from the OpenSearch index.

        :param document_set: Every document of the document set.
        """
        if document_set.id is None:
            return
        # Use transaction: log row deletion needs deleted document ids.
        async with get_transaction_cursor() as cur:
            ids = await delete_dropped_documents(
                cur,
                document_set.id,
                [doc.id for doc in document_set.documents],
                document_set.modified_at,
            )
            await delete_document_logs(cur, ids)
            if ids:
                # The next sync deletes these from the index only if the set has a
                # pending document. A sync since the set was stored may have left none.
                await reset_synced_at_by_set(cur, document_set.id)
        if ids:
            logger.info(
                "Deleted %d document(s) no longer in document set %s from the database.",
                len(ids),
                document_set.id,
            )
