import asyncio
import logging
from abc import ABC, abstractmethod
from datetime import datetime, timezone

from search_api.database.repository import get_cursor
from search_api.services.snomed import SnomedService

logger = logging.getLogger(__name__)


class SnomedTermCacheService(ABC):
    """Persistent cache mapping indexed SNOMED CT concept IDs to preferred terms."""

    @abstractmethod
    async def load(self) -> None:
        """Populate the cache from the backing store."""

    @abstractmethod
    async def get_preferred_terms(self, concept_ids: set[str]) -> dict[str, str]:
        """Return preferred terms for the given concept IDs.

        Args:
            concept_ids: SNOMED CT concept IDs to look up.

        Returns:
            Mapping of concept ID to preferred term. IDs not in the store
            are omitted from the result.
        """

    @abstractmethod
    async def cache_preferred_terms(
        self, concept_ids: set[str], snomed: SnomedService
    ) -> None:
        """Resolve and store preferred terms for any concept IDs not already
        in the cache.

        Concept IDs that are already present are left unchanged.

        Args:
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

    def __init__(self, table_name: str, refresh_interval: float = 300.0) -> None:
        self._table_name = table_name
        self._refresh_interval = refresh_interval
        self._cache: dict[str, str] = {}
        self._last_refreshed: datetime | None = None
        self._task: asyncio.Task | None = None

    async def load(self) -> None:
        """Load all terms from Postgres into the in-memory cache.

        Call this once at startup before serving requests.
        """
        async with get_cursor() as cur:
            await cur.execute(
                f"SELECT concept_id, preferred_term FROM {self._table_name}"
            )
            self._cache = {row[0]: row[1] for row in await cur.fetchall()}
        self._last_refreshed = datetime.now(timezone.utc)
        logger.info(
            "Loaded %d SNOMED preferred term(s) into memory cache.", len(self._cache)
        )

    async def _has_changes_since(self, since: datetime) -> bool:
        async with get_cursor() as cur:
            await cur.execute(
                f"SELECT 1 FROM {self._table_name} WHERE updated_at > %s LIMIT 1",
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

    async def get_preferred_terms(self, concept_ids: set[str]) -> dict[str, str]:
        return {
            cid: term
            for cid in concept_ids
            if (term := self._cache.get(cid)) is not None
        }

    async def cache_preferred_terms(
        self, concept_ids: set[str], snomed: SnomedService
    ) -> None:
        if not concept_ids:
            return

        missing = concept_ids.difference(self._cache)
        if not missing:
            return

        logger.info("Resolving %d new concept ID(s) from Snowstorm.", len(missing))
        terms = await snomed.get_preferred_terms(missing)
        if not terms:
            return

        async with get_cursor() as cur:
            await cur.executemany(
                f"""
                INSERT INTO {self._table_name} (concept_id, preferred_term, updated_at)
                VALUES (%s, %s, now())
                ON CONFLICT (concept_id) DO NOTHING
                """,
                [(cid, term) for cid, term in terms.items()],
            )
        self._cache.update(terms)
        logger.info("Cached preferred terms for %d concept ID(s).", len(terms))

    async def refresh(self, snomed: SnomedService) -> None:
        async with get_cursor() as cur:
            await cur.execute(f"SELECT concept_id FROM {self._table_name}")
            all_ids = {row[0] for row in await cur.fetchall()}

        if not all_ids:
            logger.info("No concept IDs stored — nothing to refresh.")
            return

        logger.info("Refreshing preferred terms for %d concept ID(s).", len(all_ids))
        terms = await snomed.get_preferred_terms(all_ids)

        async with get_cursor() as cur:
            await cur.executemany(
                f"""
                UPDATE {self._table_name}
                SET preferred_term = %s, updated_at = now()
                WHERE concept_id = %s
                """,
                [(term, cid) for cid, term in terms.items()],
            )
        self._cache.update(terms)
        logger.info("Refreshed %d preferred term(s).", len(terms))
