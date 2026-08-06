import asyncio
import logging
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterable
from datetime import datetime, timezone

from search_api.database.repository import get_cursor
from search_api.exceptions import SystemException
from search_api.services.ontology.service import (
    OntologyService,
    TermCache,
    normalise_term,
)

logger = logging.getLogger(__name__)

TERMS_CACHE_TABLE = "terms_cache"

_BATCH_SIZE = 1000

type PreferredTermByFieldAndConceptIdMap = dict[str, dict[str, str]]
type ConceptIdsByFieldAndPreferredTermMap = dict[str, dict[str, set[str]]]

# A factory that builds a fully-configured term cache for one ontology.
type TermCacheFactory = Callable[[], "OntologyTermCacheService"]


class OntologyTermCacheService(TermCache, ABC):
    """Persistent cache mapping indexed concept IDs to preferred terms."""

    @abstractmethod
    async def load(self) -> None:
        """Populate the cache from the backing store."""

    @abstractmethod
    async def get_preferred_terms(
        self, field_id: str, concept_ids: set[str]
    ) -> dict[str, str]:
        """Return preferred terms for concept IDs that are in the cache for field_id.

        Args:
            field_id: Field ID.
            concept_ids: Concept IDs to look up.

        Returns:
            Mapping of concept ID to preferred term. IDs not in the store are omitted.
        """

    @abstractmethod
    async def cache_preferred_terms(
        self, field_id: str, concept_ids: set[str], ontology: OntologyService
    ) -> None:
        """Resolve and store preferred terms for any concept IDs not already in the cache.

        Concept IDs that are already present are left unchanged.

        Args:
            field_id: Field ID the concept IDs belong to.
            concept_ids: Concept IDs that should be in the cache.
            ontology: Ontology service used to resolve concept IDs.
        """

    @abstractmethod
    async def get_concept_ids_by_term(self, field_id: str, term: str) -> set[str]:
        """Return the concept ids cached for a field under term.

        Matched case- and space-insensitively.
        """

    @abstractmethod
    async def refresh(self, ontology: OntologyService) -> None:
        """Resolve all stored concept IDs against the current ontology release.

        Updates stored preferred terms with the latest value from the ontology
        service. Use after a release to keep preferred terms current.

        Args:
            ontology: Ontology service used to look up updated preferred terms.
        """

    def start(self) -> None:
        """Start any background work. No-op by default; the app lifespan calls this."""

    def stop(self) -> None:
        """Stop any background work. No-op by default; the app lifespan calls this."""


class PostgresOntologyTermCacheService(OntologyTermCacheService):
    """Postgres-backed term cache parameterised by ontology id.

    All ontologies share the ``terms_cache`` table, distinguished by
    ``ontology_id``. Reads are served from an in-memory dict populated at
    startup and reloaded from Postgres in the background every
    ``refresh_interval`` seconds. Writes update both Postgres and the
    in-memory dict.
    """

    def __init__(self, ontology_id: str, refresh_interval: float = 300.0) -> None:
        self._ontology_id = ontology_id
        self._refresh_interval = refresh_interval
        self._preferred_term_by_id: PreferredTermByFieldAndConceptIdMap = {}
        self._ids_by_preferred_term: ConceptIdsByFieldAndPreferredTermMap = {}
        self._last_refreshed: datetime | None = None
        self._task: asyncio.Task | None = None

    def _index_term(self, field_id: str, concept_id: str, preferred_term: str) -> None:
        """Map concept_id and preferred_term to each other in both directions."""
        preferred_term_by_id = self._preferred_term_by_id.setdefault(field_id, {})
        ids_by_preferred_term = self._ids_by_preferred_term.setdefault(field_id, {})
        existing_preferred_term = preferred_term_by_id.get(concept_id)
        if existing_preferred_term is not None:
            # Remove existing preferred term to concept id mapping.
            ids_by_preferred_term[normalise_term(existing_preferred_term)].discard(
                concept_id
            )

        preferred_term_by_id[concept_id] = preferred_term
        ids_by_preferred_term.setdefault(normalise_term(preferred_term), set()).add(
            concept_id
        )

    async def load(self) -> None:
        """Load all terms from Postgres into the in-memory cache.

        Call this once at startup before serving requests.
        """
        async with get_cursor() as cur:
            await cur.execute(
                f"SELECT field_id, concept_id, preferred_term FROM {TERMS_CACHE_TABLE} "
                f"WHERE ontology_id = %s",
                (self._ontology_id,),
            )
            rows = await cur.fetchall()
        self._preferred_term_by_id = {}
        self._ids_by_preferred_term = {}
        for field_id, concept_id, preferred_term in rows:
            self._index_term(field_id, concept_id, preferred_term)
        self._last_refreshed = datetime.now(timezone.utc)
        logger.info("Loaded %d preferred term(s) into memory cache.", len(rows))

    async def get_concept_ids_by_term(self, field_id: str, term: str) -> set[str]:
        return set(
            self._ids_by_preferred_term.get(field_id, {}).get(normalise_term(term), ())
        )

    async def _has_changes_since(self, since: datetime) -> bool:
        async with get_cursor() as cur:
            await cur.execute(
                f"SELECT 1 FROM {TERMS_CACHE_TABLE} "
                f"WHERE ontology_id = %s AND updated_at > %s LIMIT 1",
                (self._ontology_id, since),
            )
            return await cur.fetchone() is not None

    def start(self) -> None:
        """Start the background task that periodically reloads the cache from Postgres."""
        if self._task is not None and not self._task.done():
            logger.warning("Term cache refresh task is already running.")
            return
        self._task = asyncio.create_task(self._refresh_loop())

    def stop(self) -> None:
        """Cancel the background refresh task."""
        if self._task is not None:
            self._task.cancel()
            self._task = None

    async def _refresh_loop(self) -> None:
        while True:
            await asyncio.sleep(self._refresh_interval)
            try:
                if self._last_refreshed and not await self._has_changes_since(
                    self._last_refreshed
                ):
                    continue
                await self.load()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Failed to reload term cache from Postgres.")

    async def get_preferred_terms(
        self, field_id: str, concept_ids: set[str]
    ) -> dict[str, str]:
        preferred_term_by_id = self._preferred_term_by_id.get(field_id, {})
        return {
            concept_id: preferred_term
            for concept_id in concept_ids
            if (preferred_term := preferred_term_by_id.get(concept_id)) is not None
        }

    async def cache_preferred_terms(
        self, field_id: str, concept_ids: set[str], ontology: OntologyService
    ) -> None:
        if not concept_ids:
            return

        missing = concept_ids.difference(self._preferred_term_by_id.get(field_id, {}))
        if not missing:
            return

        logger.info("Resolving %d new concept ID(s) from the ontology.", len(missing))
        terms = await ontology.get_preferred_terms(missing)
        if not terms:
            return

        rows = [
            (self._ontology_id, concept_id, field_id, terms[concept_id])
            for concept_id in missing
            if concept_id in terms
        ]
        if not rows:
            return

        async with get_cursor() as cur:
            for i in range(0, len(rows), _BATCH_SIZE):
                await cur.executemany(
                    f"""
                    INSERT INTO {TERMS_CACHE_TABLE}
                        (ontology_id, concept_id, field_id, preferred_term, updated_at)
                    VALUES (%s, %s, %s, %s, now())
                    ON CONFLICT (ontology_id, concept_id, field_id) DO NOTHING
                    """,
                    rows[i : i + _BATCH_SIZE],
                )

        for _, concept_id, _, preferred_term in rows:
            self._index_term(field_id, concept_id, preferred_term)

        logger.info(
            "Cached preferred terms for %d (concept_id, field_id) pair(s).", len(rows)
        )

    async def refresh(self, ontology: OntologyService) -> None:
        async with get_cursor() as cur:
            await cur.execute(
                f"SELECT field_id, concept_id FROM {TERMS_CACHE_TABLE} WHERE ontology_id = %s",
                (self._ontology_id,),
            )
            rows = await cur.fetchall()

        if not rows:
            logger.info("No concept IDs stored — nothing to refresh.")
            return

        by_field: dict[str, set[str]] = {}
        for field_id, concept_id in rows:
            by_field.setdefault(field_id, set()).add(concept_id)

        total_updated = 0
        for field_id, concept_ids in by_field.items():
            terms = await ontology.get_preferred_terms(concept_ids)
            if not terms:
                logger.warning(
                    "No preferred terms returned from the ontology for field '%s'.",
                    field_id,
                )
                continue
            preferred_term_by_id = self._preferred_term_by_id.get(field_id, {})
            to_update = [
                (preferred_term, self._ontology_id, concept_id, field_id)
                for concept_id, preferred_term in terms.items()
                if preferred_term_by_id.get(concept_id) != preferred_term
            ]
            if not to_update:
                continue
            async with get_cursor() as cur:
                for i in range(0, len(to_update), _BATCH_SIZE):
                    await cur.executemany(
                        f"""
                        UPDATE {TERMS_CACHE_TABLE}
                        SET preferred_term = %s, updated_at = now()
                        WHERE ontology_id = %s AND concept_id = %s AND field_id = %s
                        """,
                        to_update[i : i + _BATCH_SIZE],
                    )
            for preferred_term, _, concept_id, _ in to_update:
                self._index_term(field_id, concept_id, preferred_term)
            total_updated += len(to_update)

        logger.info("Refreshed %d preferred term(s).", total_updated)


# Registry of preferred term cache factories, keyed by ontology id (e.g. ``SCTID``).
# Registered via register_term_cache in services/ontologies.py.
_TERM_CACHE_FACTORIES: dict[str, TermCacheFactory] = {}


def register_term_cache(ontology_id: str, factory: TermCacheFactory) -> None:
    """Register the term-cache factory for an ontology id.

    Called by each provider module at import time.
    """
    _TERM_CACHE_FACTORIES[ontology_id] = factory


def create_term_caches(
    ontology_ids: Iterable[str],
) -> dict[str, OntologyTermCacheService]:
    """Create one preferred term cache per ontology id, keyed by that id.

    The cache is per ontology. Callers map a field to its
    ontology id and look the cache up here, so every field resolving against
    the same ontology shares the one cache instance for that id.

    :raises SystemException: if no term-cache factory is registered for an id,
        e.g. when the provider module has not been imported.
    """
    caches: dict[str, OntologyTermCacheService] = {}
    for ontology_id in ontology_ids:
        try:
            factory = _TERM_CACHE_FACTORIES[ontology_id]
        except KeyError:
            raise SystemException(
                f"No term cache registered for ontology id {ontology_id!r}. "
                f"Registered: {', '.join(sorted(_TERM_CACHE_FACTORIES)) or '(none)'}."
            )
        caches[ontology_id] = factory()
    return caches
