"""Ontology cached in ontology_cache table."""

import logging
from abc import ABC, abstractmethod
from typing import override

from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict

from search_api.api.beacon.models import BeaconFilteringTerm
from search_api.database.repository import get_cursor
from search_api.services.ontology import OntologyService

logger = logging.getLogger(__name__)

ONTOLOGY_CACHE_TABLE = "ontology_cache"


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


class CachedOntologyStore(ABC):
    """Reads and writes the cached ontology."""

    @abstractmethod
    async def read(self) -> CachedOntology | None: ...

    @abstractmethod
    async def write(self, fetched: CachedOntology) -> None: ...


class PostgresOntologyStore(CachedOntologyStore):
    """Reads and writes the cached ontology in the ontology_cache table."""

    def __init__(self, ontology_id: str) -> None:
        self._ontology_id = ontology_id

    async def read(self) -> CachedOntology | None:
        async with get_cursor() as cur:
            await cur.execute(
                f"SELECT version, sha256, data FROM {ONTOLOGY_CACHE_TABLE} "
                f"WHERE ontology_id = %s",
                (self._ontology_id,),
            )
            row = await cur.fetchone()
        if row is None:
            return None
        version, sha256, data = row
        concepts = [CachedOntologyConcept.model_validate(c) for c in data]
        return CachedOntology(version=version, sha256=sha256, concepts=concepts)

    async def write(self, fetched: CachedOntology) -> None:
        data = [c.model_dump(mode="json") for c in fetched.concepts]
        async with get_cursor() as cur:
            await cur.execute(
                f"""
                INSERT INTO {ONTOLOGY_CACHE_TABLE}
                    (ontology_id, version, sha256, data, updated_at)
                VALUES (%s, %s, %s, %s, now())
                ON CONFLICT (ontology_id) DO UPDATE
                SET version = EXCLUDED.version,
                    sha256 = EXCLUDED.sha256,
                    data = EXCLUDED.data,
                    updated_at = EXCLUDED.updated_at
                """,
                (self._ontology_id, fetched.version, fetched.sha256, Jsonb(data)),
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


class CachedOntologyService(OntologyService):
    """Cached ontology service.

    Initialised at startup without automatic refresh.
    """

    def __init__(self, source: BootstrapCachedOntologySource) -> None:
        self._source = source
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
                by_value.setdefault(value.casefold(), set()).add(concept.concept_id)
            for parent_id in concept.parent_ids:
                children.setdefault(parent_id, set()).add(concept.concept_id)
        self._version = cached.version
        self._by_id = by_id
        self._by_value = by_value
        self._children = children

    @override
    async def init(self) -> None:
        self._set_concepts(await self._source.fetch())

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
    async def _resolve_concept_ids(
        self, value: str, filtering_term: BeaconFilteringTerm
    ) -> set[str]:
        return set(self._by_value.get(value.casefold(), ()))

    @override
    async def _resolve_descendant_ids(self, concept_ids: set[str]) -> set[str]:
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
