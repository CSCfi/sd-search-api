import asyncio
import logging
from abc import ABC, abstractmethod
from datetime import datetime, timezone

from search_api.database.repository import get_cursor
from search_api.services.snomed import SnomedService

logger = logging.getLogger(__name__)

SNOMED_TABLE = "snomed"

_BATCH_SIZE = 1000

type SnomedTermCache = dict[str, dict[str, str]]


class SnomedTermCacheService(ABC):
    """Persistent cache mapping indexed SNOMED CT concept IDs to preferred terms."""

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
            concept_ids: SNOMED CT concept IDs to look up.

        Returns:
            Mapping of concept ID to preferred term.  IDs not in the store
            are omitted from the result.
        """

    @abstractmethod
    async def cache_preferred_terms(
        self, field_id: str, concept_ids: set[str], snomed: SnomedService
    ) -> None:
        """Resolve and store preferred terms for any concept IDs not already
        in the cache.

        Concept IDs that are already present are left unchanged.

        Args:
            field_id: Field ID the concept IDs belong to.
            concept_ids: SNOMED CT concept IDs that should be in the cache.
            snomed: SNOMED service used to resolve concept IDs.
        """

    @abstractmethod
    async def refresh(self, snomed: SnomedService) -> None:
        """Resolve all stored concept IDs against the current SNOMED release.

        Updates stored preferred terms with the latest value from Snowstorm.
        Use this after a SNOMED release to keep preferred terms current.

        Args:
            snomed: SNOMED service used to look up updated preferred terms.
        """


class PostgresSnomedTermCacheService(SnomedTermCacheService):
    """Persistent Postgres cache mapping indexed SNOMED CT concept IDs to preferred terms.

    Reads are served from an in-memory dict populated at startup and reloaded
    from Postgres in the background every ``refresh_interval`` seconds.
    Writes (from sync and term refresh) update both Postgres and the in-memory
    dict.
    """

    def __init__(self, refresh_interval: float = 300.0) -> None:
        self._refresh_interval = refresh_interval
        self._cache: SnomedTermCache = {}
        self._last_refreshed: datetime | None = None
        self._task: asyncio.Task | None = None

    async def load(self) -> None:
        """Load all terms from Postgres into the in-memory cache.

        Call this once at startup before serving requests.
        """
        async with get_cursor() as cur:
            await cur.execute(
                f"SELECT field_id, concept_id, preferred_term FROM {SNOMED_TABLE}"
            )
            rows = await cur.fetchall()
        cache: SnomedTermCache = {}
        for field_id, concept_id, term in rows:
            cache.setdefault(field_id, {})[concept_id] = term
        self._cache = cache
        self._last_refreshed = datetime.now(timezone.utc)
        logger.info("Loaded %d SNOMED preferred term(s) into memory cache.", len(rows))

    async def _has_changes_since(self, since: datetime) -> bool:
        async with get_cursor() as cur:
            await cur.execute(
                f"SELECT 1 FROM {SNOMED_TABLE} WHERE updated_at > %s LIMIT 1",
                (since,),
            )
            return await cur.fetchone() is not None

    def start(self) -> None:
        """Start the background task that periodically reloads the cache from Postgres."""
        if self._task is not None and not self._task.done():
            logger.warning("SNOMED term cache refresh task is already running.")
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
                logger.exception("Failed to reload SNOMED term cache from Postgres.")

    async def get_preferred_terms(
        self, field_id: str, concept_ids: set[str]
    ) -> dict[str, str]:
        field_cache = self._cache.get(field_id, {})
        return {
            concept_id: term
            for concept_id in concept_ids
            if (term := field_cache.get(concept_id)) is not None
        }

    async def cache_preferred_terms(
        self, field_id: str, concept_ids: set[str], snomed: SnomedService
    ) -> None:
        if not concept_ids:
            return

        missing = concept_ids.difference(self._cache.get(field_id, {}))
        if not missing:
            return

        logger.info("Resolving %d new concept ID(s) from Snowstorm.", len(missing))
        terms = await snomed.get_preferred_terms(missing)
        if not terms:
            return

        rows = [
            (concept_id, field_id, terms[concept_id])
            for concept_id in missing
            if concept_id in terms
        ]
        if not rows:
            return

        async with get_cursor() as cur:
            for i in range(0, len(rows), _BATCH_SIZE):
                await cur.executemany(
                    f"""
                    INSERT INTO {SNOMED_TABLE} (concept_id, field_id, preferred_term, updated_at)
                    VALUES (%s, %s, %s, now())
                    ON CONFLICT (concept_id, field_id) DO NOTHING
                    """,
                    rows[i : i + _BATCH_SIZE],
                )

        for concept_id, _, term in rows:
            self._cache.setdefault(field_id, {})[concept_id] = term

        logger.info(
            "Cached preferred terms for %d (concept_id, field_id) pair(s).", len(rows)
        )

    async def refresh(self, snomed: SnomedService) -> None:
        async with get_cursor() as cur:
            await cur.execute(f"SELECT field_id, concept_id FROM {SNOMED_TABLE}")
            rows = await cur.fetchall()

        if not rows:
            logger.info("No concept IDs stored — nothing to refresh.")
            return

        by_field: dict[str, set[str]] = {}
        for field_id, concept_id in rows:
            by_field.setdefault(field_id, set()).add(concept_id)

        total_updated = 0
        for field_id, concept_ids in by_field.items():
            terms = await snomed.get_preferred_terms(concept_ids)
            if not terms:
                logger.warning(
                    "No preferred terms returned from Snowstorm for field '%s'.",
                    field_id,
                )
                continue
            field_cache = self._cache.get(field_id, {})
            to_update = [
                (term, concept_id, field_id)
                for concept_id, term in terms.items()
                if field_cache.get(concept_id) != term
            ]
            if not to_update:
                continue
            async with get_cursor() as cur:
                for i in range(0, len(to_update), _BATCH_SIZE):
                    await cur.executemany(
                        f"""
                        UPDATE {SNOMED_TABLE}
                        SET preferred_term = %s, updated_at = now()
                        WHERE concept_id = %s AND field_id = %s
                        """,
                        to_update[i : i + _BATCH_SIZE],
                    )
            for term, concept_id, _ in to_update:
                self._cache.setdefault(field_id, {})[concept_id] = term
            total_updated += len(to_update)

        logger.info("Refreshed %d preferred term(s).", total_updated)
