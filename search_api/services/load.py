"""Generic load service: store extracted documents and cache ontology preferred terms."""

import logging
from collections.abc import Iterator, Sequence

from psycopg import AsyncCursor

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
from search_api.database.repository import get_cursor
from search_api.services.ontology.service import (
    OntologyService,
    get_ontology_id_by_field,
    get_ontology_service,
)
from search_api.exceptions import UserException
from search_api.services.ontology.term_cache import OntologyTermCache

logger = logging.getLogger(__name__)


def ontology_services_by_field(
    filtering_terms: Sequence[BeaconFilteringTerm],
) -> dict[str, OntologyService]:
    """Map each ontology field id to its provider, selected by ``ontology.id``."""
    return {
        field_id: get_ontology_service(ontology_id)
        for field_id, ontology_id in get_ontology_id_by_field(filtering_terms).items()
    }


def concept_ids_from_values(
    values: list[OpenSearchFieldValue],
    ontology_by_field: dict[str, OntologyService],
) -> dict[str, set[str]]:
    """Return concept IDs grouped by field id, from ontology field values."""
    result: dict[str, set[str]] = {}
    for fv in values:
        provider = ontology_by_field.get(fv.field.id)
        if (
            provider is not None
            and isinstance(fv.value, str)
            and provider.is_concept_id(fv.value)
        ):
            result.setdefault(fv.field.id, set()).add(fv.value)
    return result


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

                for field_id, concept_ids in concept_ids_from_values(
                    doc.all_values, self._ontology_by_field
                ).items():
                    ontology_id = self._ontology_id_by_field[field_id]
                    await self._term_caches[ontology_id].cache_preferred_terms(
                        field_id, concept_ids, self._ontology_by_field[field_id]
                    )

        logger.info("Done — loaded %d, skipped %d document(s).", loaded, skipped)
