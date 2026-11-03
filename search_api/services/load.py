"""Generic load service: store extracted documents and cache ontology preferred terms."""

import logging
from collections.abc import Iterator, Sequence

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
from search_api.database.document import get_modified_at, upsert_document
from search_api.database.document_log import delete_document_logs, write_document_log
from search_api.database.models import StoredDocumentLog
from search_api.database.repository import get_cursor
from search_api.exceptions import SystemException, UserException
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

    async def store_document(self, cur: AsyncCursor, doc: ExtractedDocument) -> None:
        """Store one extracted document to the database.

        :raises UserException: if the document does not match the deployment.
        """
        self.validate_document(doc)
        await upsert_document(cur, doc.id, build_document(doc), doc.modified_at)

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

    async def store_documents(self, docs_iter: Iterator[ExtractedDocument]) -> None:
        """
        Store extracted documents to the database.

        Documents that are not newer than what is already stored are skipped.
        Preferred terms for ontology concepts are stored in the term cache for
        the concept's ontology.

        :param docs_iter: Iterator of extracted documents.
        """
        await self._initialise()

        loaded = 0
        skipped = 0

        async with get_cursor() as cur:
            for doc in docs_iter:
                existing = await get_modified_at(cur, doc.id)
                if (
                    existing is not None
                    and doc.modified_at is not None
                    and existing >= doc.modified_at
                ):
                    logger.debug(
                        "Skipping document %s — not newer than stored.", doc.id
                    )
                    skipped += 1
                    continue

                # A document loaded again replaces its log rows.
                await delete_document_logs(cur, doc.id)

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
                await self.store_document(cur, doc)
                await cache_concept_terms(doc, self._ontology_bindings)
                loaded += 1
                logger.debug("Loaded document %s.", doc.id)

        logger.info("Done — loaded %d, skipped %d document(s).", loaded, skipped)
