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
from search_api.api.opensearch.models import ExtractedDocument
from search_api.api.qualifiers import validate_requested_qualifiers
from search_api.api.scopes import validate_document_scope
from search_api.database.document import get_modified_at, upsert_document
from search_api.database.repository import get_cursor
from search_api.exceptions import UserException
from search_api.services.ontology.term_cache import OntologyTermCache
from search_api.services.ontology.values import (
    cache_concept_terms,
    get_ontology_bindings,
    resolve_concepts,
)

logger = logging.getLogger(__name__)


class LoadService:
    """Store extracted documents and cache their ontology preferred terms."""

    def __init__(
        self,
        term_caches: dict[str, OntologyTermCache],
        filtering_terms: Sequence[BeaconFilteringTerm],
        filtering_scopes: Sequence[BeaconFilteringScope] = (),
        filtering_qualifiers: Sequence[BeaconFilteringQualifier] = (),
        replace_concepts: bool = True,
    ) -> None:
        self._term_caches = term_caches
        self._ontology_bindings = get_ontology_bindings(filtering_terms, term_caches)
        self._filtering_scopes = filtering_scopes
        self._filtering_qualifiers = filtering_qualifiers
        self._replace_concepts = replace_concepts

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
        for ontology in {
            binding.ontology for binding in self._ontology_bindings.values()
        }:
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

                # The document's ontology values are resolved to concept
                # ids before the document is stored. Values that could not
                # be resolved are logged.
                await resolve_concepts(
                    cur, doc, self._ontology_bindings, self._replace_concepts
                )
                await self.store_document(cur, doc)
                await cache_concept_terms(cur, doc, self._ontology_bindings)
                loaded += 1
                logger.debug("Loaded document %s.", doc.id)

        logger.info("Done — loaded %d, skipped %d document(s).", loaded, skipped)
