"""Generic load service: store extracted documents and cache SNOMED preferred terms."""

import logging
from collections.abc import Iterator

from psycopg import AsyncCursor

from search_api.api.opensearch.document import build_document
from search_api.api.opensearch.models import ExtractedDocument, OpenSearchFieldValue
from search_api.database.document import get_modified_at, upsert_document
from search_api.database.repository import get_cursor
from search_api.services.snomed import SnomedService, is_concept_id
from search_api.services.snomed_term import SnomedTermCacheService

logger = logging.getLogger(__name__)


def concept_ids_from_values(
    values: list[OpenSearchFieldValue],
) -> dict[str, set[str]]:
    """Return SNOMED CT concept IDs grouped by field id, from ontology field values."""
    result: dict[str, set[str]] = {}
    for fv in values:
        if (
            fv.field.type in ("ontology", "ontologyOrValue")
            and isinstance(fv.value, str)
            and is_concept_id(fv.value)
        ):
            result.setdefault(fv.field.id, set()).add(fv.value)
    return result


class LoadService:
    """Store extracted documents and cache their SNOMED preferred terms."""

    def __init__(
        self,
        snomed_term_service: SnomedTermCacheService,
        snomed_service: SnomedService,
    ) -> None:
        self._snomed_term_service = snomed_term_service
        self._snomed_service = snomed_service

    @staticmethod
    async def store_document(cur: AsyncCursor, doc: ExtractedDocument) -> None:
        """Store one extracted document to the database."""
        await upsert_document(cur, doc.id, build_document(doc.values), doc.modified_at)

    async def store_documents(self, docs_iter: Iterator[ExtractedDocument]) -> None:
        """
        Store extracted documents to the database.

        Documents that are not newer than what is already stored are skipped. Preferred terms for
        SNOMED CT concepts are stored in the SNOMED term cache.

        :param docs_iter: Iterator of extracted documents.
        """
        await self._snomed_term_service.load()

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
                    logger.info("Skipping document %s — not newer than stored.", doc.id)
                    skipped += 1
                    continue

                await LoadService.store_document(cur, doc)
                loaded += 1
                logger.info("Loaded document %s.", doc.id)

                for field_id, concept_ids in concept_ids_from_values(
                    doc.values
                ).items():
                    await self._snomed_term_service.cache_preferred_terms(
                        field_id, concept_ids, self._snomed_service
                    )

        logger.info("Done — loaded %d, skipped %d document(s).", loaded, skipped)
