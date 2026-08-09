import asyncio
import logging
from datetime import datetime
from typing import override

from search_api.api.beacon.models import BeaconFilteringTerm
from search_api.services.ontology.cache.models import (
    CachedOntology,
    CachedOntologyConcept,
)
from search_api.services.ontology.cache.source import OntologySource
from search_api.services.ontology.cache.store import OntologyCacheStore
from search_api.services.ontology.service import OntologyService, normalise_term

logger = logging.getLogger(__name__)


class CachedOntologyService(OntologyService):
    """An ontology service serving a whole cached ontology from memory.

    Loaded into memory from the store. The store is filled from the
    source if it is empty. The store is reloaded into memory when
    it is updated.
    """

    def __init__(
        self,
        store: OntologyCacheStore,
        source: OntologySource,
        refresh_interval: float = 300.0,
    ) -> None:
        self._store = store
        self._source = source
        self._refresh_interval = refresh_interval
        self._updated_at: datetime | None = None
        self._task: asyncio.Task | None = None
        self._version: str | None = None
        self._by_id: dict[str, CachedOntologyConcept] = {}
        self._by_value: dict[
            str, set[str]
        ] = {}  # concept ids by casefolded concept id, preferred term or synonym
        self._children: dict[
            str, set[str]
        ] = {}  # child concept ids by parent concept id

    def _set_concepts(self, cached: CachedOntology) -> None:
        by_id: dict[str, CachedOntologyConcept] = {}
        by_value: dict[str, set[str]] = {}
        children: dict[str, set[str]] = {}
        for concept in cached.concepts:
            by_id[concept.concept_id] = concept
            # A concept is resolved by its id just like by its terms, so that
            # all of them match case-insensitively.
            for value in (
                concept.concept_id,
                concept.preferred_term,
                *concept.synonyms,
            ):
                by_value.setdefault(normalise_term(value), set()).add(
                    concept.concept_id
                )
            for parent_id in concept.parent_ids:
                children.setdefault(parent_id, set()).add(concept.concept_id)
        self._version = cached.version
        self._by_id = by_id
        self._by_value = by_value
        self._children = children

    @override
    async def init(self) -> None:
        stored = await self._store.read()
        if stored is None:
            # Fetch the ontology and save it into the store.
            stored = await self._source.fetch()
            await self._store.write(stored)
        self._set_concepts(stored)
        self._updated_at = await self._store.updated_at()

    @override
    def start(self) -> None:
        """Start the background task that reloads the cache when the store changes."""
        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.create_task(self._refresh_loop())

    @override
    def stop(self) -> None:
        """Stop the background task that reloads the cache when the store changes."""
        if self._task is not None:
            self._task.cancel()
            self._task = None

    async def _refresh_loop(self) -> None:
        while True:
            await asyncio.sleep(self._refresh_interval)
            try:
                updated_at = await self._store.updated_at()
                if updated_at == self._updated_at:
                    continue
                stored = await self._store.read()
                if stored is None:
                    continue
                self._set_concepts(stored)
                self._updated_at = updated_at
                logger.info("Refreshed the ontology.")
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Failed to refresh the ontology.")

    @override
    def is_concept_id(self, value: str) -> bool:
        return value in self._by_id

    @override
    async def get_preferred_terms(self, concept_ids: set[str]) -> dict[str, str]:
        return {
            concept_id: self._by_id[concept_id].preferred_term
            for concept_id in concept_ids
            if concept_id in self._by_id
        }

    @override
    async def _find_concept_ids(
        self, value: str, filtering_term: BeaconFilteringTerm
    ) -> set[str]:
        concept_ids = set(self._by_value.get(normalise_term(value), ()))

        # Keep only the concepts the field is restricted to. An
        # unrestricted field keeps all of them.
        restriction = filtering_term.ontologyRestriction
        if restriction is None:
            return concept_ids
        permitted = set(restriction.concept_ids)
        if restriction.include_descendants:
            permitted |= await self._find_descendant_ids(permitted)
        return concept_ids & permitted

    @override
    async def _find_descendant_ids(self, concept_ids: set[str]) -> set[str]:
        result: set[str] = set()
        child_ids: list[str] = []
        for concept_id in concept_ids:
            child_ids.extend(self._children.get(concept_id, ()))
        while child_ids:
            child_id = child_ids.pop()
            if child_id in result:
                continue
            result.add(child_id)
            child_ids.extend(self._children.get(child_id, ()))
        return result
