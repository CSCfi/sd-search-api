import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

logger = logging.getLogger(__name__)


class UpdatedPoller:
    """Reloads a cache when the store behind it changes.

    Calls ``refresh`` whenever ``updated_at`` returns something new.
    """

    def __init__(
        self,
        name: str,
        updated_at: Callable[[], Awaitable[Any]],
        refresh: Callable[[], Awaitable[None]],
        interval: float,
    ) -> None:
        self._name = name
        self._updated_at = updated_at
        self._refresh = refresh
        self._interval = interval
        self._last: Any = None
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        """Refresh now, then again whenever the store is updated.

        ``updated_at`` is read *before* the refresh, so a write landing during it is
        either included by that refresh or seen by the next poll. Read afterwards, a
        write in that window would be recorded as already loaded and missed.
        """
        updated_at = await self._updated_at()
        if updated_at is None:
            # Nothing is stored yet, so there is nothing to refresh.
            logger.info("Nothing stored yet, so the %s was not filled.", self._name)
        else:
            await self._refresh()
        self._last = updated_at
        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.create_task(self._poll())

    def stop(self) -> None:
        """Stop polling."""
        if self._task is not None:
            self._task.cancel()
            self._task = None

    async def _poll(self) -> None:
        while True:
            await asyncio.sleep(self._interval)
            try:
                updated_at = await self._updated_at()
                if updated_at == self._last:
                    continue
                await self._refresh()
                # Recorded after the refresh, so a failed one is retried.
                self._last = updated_at
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Failed to refresh the %s.", self._name)
