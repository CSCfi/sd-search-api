"""Generic load service: store extracted documents and cache ontology preferred terms."""

import logging
from collections.abc import Iterator, Sequence

from psycopg import AsyncCursor
from pydantic import BaseModel, Field

from search_api.api.beacon.models import (
    BeaconFilteringQualifier,
    BeaconFilteringScope,
    BeaconFilteringTerm,
)
from search_api.api.opensearch.document import build_document
from search_api.api.opensearch.models import ExtractedDocument, OpenSearchFieldValue
from search_api.api.qualifiers import validate_requested_qualifiers
from search_api.api.scopes import validate_document_scope
from search_api.database.document import get_modified_at, upsert_document
from search_api.database.document_log import insert_document_log
from search_api.database.models import LogSeverity, StoredDocumentLog
from search_api.database.repository import get_cursor
from search_api.services.ontology.service import (
    OntologyService,
    get_ontology_id_by_field,
    get_ontology_service,
)
from search_api.exceptions import UserException
from search_api.services.ontology.term_cache import OntologyTermCache

logger = logging.getLogger(__name__)

# The logging levels in the document_log table.
_LOG_LEVELS: dict[str, int] = {
    "ERROR": logging.ERROR,
    "WARNING": logging.WARNING,
}


def ontology_services_by_field(
    filtering_terms: Sequence[BeaconFilteringTerm],
) -> dict[str, OntologyService]:
    """Map each ontology field id to its provider, selected by ``ontology.id``."""
    return {
        field_id: get_ontology_service(ontology_id)
        for field_id, ontology_id in get_ontology_id_by_field(filtering_terms).items()
    }


class OntologyFieldValues(BaseModel):
    """One ontology field's values, split by if the value passes is_concept_id check."""

    concept_ids: set[str] = Field(default_factory=set)
    non_concept_ids: set[str] = Field(default_factory=set)


def ontology_field_values(
    values: list[OpenSearchFieldValue],
    ontology_by_field: dict[str, OntologyService],
) -> dict[str, OntologyFieldValues]:
    """Return every ontology field's values, split by if the value passes is_concept_id, grouped by field id."""
    result: dict[str, OntologyFieldValues] = {}
    for fv in values:
        provider = ontology_by_field.get(fv.field.id)
        if provider is None or not isinstance(fv.value, str):
            continue
        split = result.setdefault(fv.field.id, OntologyFieldValues())
        if provider.is_concept_id(fv.value):
            split.concept_ids.add(fv.value)
        else:
            split.non_concept_ids.add(fv.value)
    return result


async def _store_log_entry(
    cur: AsyncCursor,
    document_id: str,
    field_id: str,
    severity: LogSeverity,
    message: str,
) -> None:
    """Store a document log entry, and log it at the level matching its severity."""
    logger.log(
        _LOG_LEVELS[severity],
        "%s (document %s, field %s)",
        message,
        document_id,
        field_id,
    )
    await insert_document_log(
        cur,
        StoredDocumentLog(
            document_id=document_id,
            field_id=field_id,
            severity=severity,
            message=message,
        ),
    )


class LoadService:
    """Store extracted documents and cache their ontology preferred terms."""

    def __init__(
        self,
        term_caches: dict[str, OntologyTermCache],
        filtering_terms: Sequence[BeaconFilteringTerm],
        filtering_scopes: Sequence[BeaconFilteringScope] = (),
        filtering_qualifiers: Sequence[BeaconFilteringQualifier] = (),
    ) -> None:
        self._term_caches = term_caches
        self._ontology_id_by_field = get_ontology_id_by_field(filtering_terms)
        self._ontology_by_field = ontology_services_by_field(filtering_terms)
        self._filtering_scopes = filtering_scopes
        self._filtering_qualifiers = filtering_qualifiers

    def validate_document(self, doc: ExtractedDocument) -> None:
        """Check an extracted document's scope and qualifiers against the
        deployment.

        This is where a deployment's extraction meets the generic service.

        :raises UserException: if the scope or a qualifier value is not declared.
        """
        try:
            validate_document_scope(doc.scope, self._filtering_scopes)
            for group in doc.groups:
                validate_requested_qualifiers(
                    {
                        qualifier_id: [value]
                        for qualifier_id, value in group.qualifiers.items()
                    },
                    self._filtering_qualifiers,
                )
        except UserException as e:
            raise UserException(f"Document '{doc.id}': {e}") from e

    async def store_document(self, cur: AsyncCursor, doc: ExtractedDocument) -> None:
        """Store one extracted document to the database.

        :raises UserException: if the document does not match the deployment.
        """
        self.validate_document(doc)
        await upsert_document(cur, doc.id, build_document(doc), doc.modified_at)

    async def store_documents(self, docs_iter: Iterator[ExtractedDocument]) -> None:
        """
        Store extracted documents to the database.

        Documents that are not newer than what is already stored are skipped.
        Preferred terms for ontology concepts are stored in the term cache for
        the concept's ontology.

        :param docs_iter: Iterator of extracted documents.
        """
        for cache in self._term_caches.values():
            await cache.load()

        # The ontologies must be initialised before load to
        # resolve preferred terms for the terms cache.
        for ontology in set(self._ontology_by_field.values()):
            await ontology.init()

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

                await self.store_document(cur, doc)
                loaded += 1
                logger.debug("Loaded document %s.", doc.id)

                for field_id, split in ontology_field_values(
                    doc.all_values, self._ontology_by_field
                ).items():
                    ontology_id = self._ontology_id_by_field[field_id]
                    unresolved = await self._term_caches[
                        ontology_id
                    ].cache_preferred_terms(
                        field_id, split.concept_ids, self._ontology_by_field[field_id]
                    )
                    # Log error where value failed is_concept_id check.
                    for value in sorted(split.non_concept_ids):
                        await _store_log_entry(
                            cur,
                            doc.id,
                            field_id,
                            "ERROR",
                            f"Value '{value}' is no concept id of "
                            f"ontology '{ontology_id}'.",
                        )
                    # Log error where concept id was not found in the ontology.
                    for value in sorted(unresolved):
                        await _store_log_entry(
                            cur,
                            doc.id,
                            field_id,
                            "ERROR",
                            f"Value '{value}' was not found in "
                            f"ontology '{ontology_id}'.",
                        )

        logger.info("Done — loaded %d, skipped %d document(s).", loaded, skipped)
