"""Ontology cached in ontology_cache table."""

import asyncio
import logging
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Protocol, override

from pydantic import BaseModel, ConfigDict

from search_api.api.beacon.models import BeaconFilteringTerm
from search_api.database.models import StoredOntology
from search_api.database.ontology_cache import (
    read_ontology,
    read_updated_at,
    write_ontology,
)
from search_api.services.ontology.service import OntologyService, normalise_term

logger = logging.getLogger(__name__)


class CachedOntologyConcept(BaseModel):
    """A cached concept."""

    model_config = ConfigDict(frozen=True)

    concept_id: str
    preferred_term: str
    synonyms: frozenset[str] = frozenset()
    parent_ids: frozenset[str] = frozenset()


class CachedOntology(BaseModel):
    """A cached ontology..

    ``version`` is the ontology's version or date.
    ``sha256`` is the hash of the fetched content.
    """

    version: str
    sha256: str
    concepts: list[CachedOntologyConcept]


class CachedOntologySource(ABC):
    """Fetches the ontology for caching from a remote source."""

    @abstractmethod
    async def fetch(self) -> CachedOntology:
        """Fetch the latest ontology version from a remote source."""

    @abstractmethod
    def is_newer(self, version: str, other: str) -> bool:
        """Return True if version is newer than other.

        A version string is only meaningful to the source that produced it,
        so each source compares its own.
        """


class CachedOntologyStore(Protocol):
    """Reads and writes the cached ontology.

    ``updated_at`` lets a reader poll for another process's write without reading
    the ontology itself, which is a whole concept table.
    """

    async def read(self) -> CachedOntology | None: ...

    async def write(self, fetched: CachedOntology) -> None: ...

    async def updated_at(self) -> datetime | None: ...


class DatabaseOntologyStore:
    """Stores one ontology's concepts in the ontology_cache table."""

    def __init__(self, ontology_id: str) -> None:
        self._ontology_id = ontology_id

    async def read(self) -> CachedOntology | None:
        stored = await read_ontology(self._ontology_id)
        if stored is None:
            return None
        return CachedOntology(
            version=stored.version,
            sha256=stored.sha256,
            concepts=[
                CachedOntologyConcept.model_validate(concept)
                for concept in stored.concepts
            ],
        )

    async def updated_at(self) -> datetime | None:
        return await read_updated_at(self._ontology_id)

    async def write(self, fetched: CachedOntology) -> None:
        await write_ontology(
            self._ontology_id,
            StoredOntology(
                version=fetched.version,
                sha256=fetched.sha256,
                concepts=[
                    concept.model_dump(mode="json") for concept in fetched.concepts
                ],
            ),
        )
        logger.info(
            "Stored %d concept(s) for ontology '%s' version '%s' sha256 '%s'.",
            len(fetched.concepts),
            self._ontology_id,
            fetched.version,
            fetched.sha256,
        )


class BootstrapCachedOntologySource(CachedOntologySource):
    """Uses or fetches a cached ontology.

    Uses an existing ontology cache. Fetches and caches
    the ontology if nothing is cached yet.
    """

    def __init__(
        self, store: CachedOntologyStore, source: CachedOntologySource
    ) -> None:
        self._store = store
        self._source = source

    async def fetch(self) -> CachedOntology:
        stored = await self._store.read()
        if stored is not None:
            return stored
        fetched = await self._source.fetch()
        await self._store.write(fetched)
        return fetched

    def is_newer(self, version: str, other: str) -> bool:
        return self._source.is_newer(version, other)

    async def updated_at(self) -> datetime | None:
        return await self._store.updated_at()


class CachedOntologyService(OntologyService):
    """Cached ontology service.

    Loaded at startup and reloaded when the stored ontology changes.
    """

    def __init__(
        self, source: BootstrapCachedOntologySource, refresh_interval: float = 300.0
    ) -> None:
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
        self._set_concepts(await self._source.fetch())
        self._updated_at = await self._source.updated_at()

    @override
    def start(self) -> None:
        """Start the background task that refreshes the cache when it changes."""
        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.create_task(self._refresh_loop())

    @override
    def stop(self) -> None:
        """Stop the background task that refreshes the cache when it changes."""
        if self._task is not None:
            self._task.cancel()
            self._task = None

    async def _refresh_loop(self) -> None:
        while True:
            await asyncio.sleep(self._refresh_interval)
            try:
                updated_at = await self._source.updated_at()
                if updated_at is None or updated_at == self._updated_at:
                    continue
                self._set_concepts(await self._source.fetch())
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
