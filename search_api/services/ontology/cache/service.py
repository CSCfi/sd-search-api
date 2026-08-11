import logging
from typing import override

from search_api.api.beacon.models import BeaconFilteringTerm
from search_api.exceptions import SystemException
from search_api.services.ontology.cache.models import (
    CachedOntology,
    CachedOntologyConcept,
)
from search_api.services.ontology.cache.source import OntologySource
from search_api.services.ontology.cache.store import OntologyCacheStore
from search_api.services.ontology.service import OntologyService, normalise_term
from search_api.services.poller import UpdatedPoller

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
        self._poller = UpdatedPoller(
            "ontology",
            lambda: store.updated_at(),
            lambda: self._reload(),
            refresh_interval,
        )
        self._version: str | None = None
        self._by_id: dict[str, CachedOntologyConcept] = {}
        self._by_value: dict[
            str, set[str]
        ] = {}  # concept ids by casefolded concept id, preferred term or synonym
        self._children: dict[
            str, set[str]
        ] = {}  # child concept ids by parent concept id
        self._initialised = False

    def _require_initialised(self) -> None:
        """Raises SystemException: if ``init`` has not been called."""
        if not self._initialised:
            raise SystemException(
                f"The {self._store.ontology_id} ontology has not been initialised."
            )

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
        self._initialised = True

    @override
    async def init(self) -> None:
        stored = await self._store.read()
        if stored is None:
            # Fetch the ontology and save it into the store.
            stored = await self._source.fetch()
            await self._store.write(stored)
        self._set_concepts(stored)

    @override
    async def start(self) -> None:
        """Start the background task that reloads the cache when the store changes."""
        await self._poller.start()

    @override
    def stop(self) -> None:
        """Stop the background task that reloads the cache when the store changes."""
        self._poller.stop()

    async def _reload(self) -> None:
        """Reload the stored ontology into the cache."""
        stored = await self._store.read()
        if stored is None:
            return
        self._set_concepts(stored)
        logger.info("Refreshed the ontology.")

    @override
    def is_concept_id(self, value: str) -> bool:
        self._require_initialised()
        return value in self._by_id

    @override
    async def get_preferred_terms(self, concept_ids: set[str]) -> dict[str, str]:
        self._require_initialised()
        return {
            concept_id: self._by_id[concept_id].preferred_term
            for concept_id in concept_ids
            if concept_id in self._by_id
        }

    @override
    async def _find_concept_ids(
        self, value: str, filtering_term: BeaconFilteringTerm
    ) -> set[str]:
        self._require_initialised()
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
        self._require_initialised()
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
