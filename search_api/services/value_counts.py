import asyncio
import logging
from collections.abc import Iterator, Sequence
from datetime import datetime

from search_api.api.beacon.services import OpenSearchBeaconService
from search_api.api.models import ValueCountsKey
from search_api.database.document import max_synced_at
from search_api.database.repository import get_cursor
from search_api.services.poller import UpdatedPoller

logger = logging.getLogger(__name__)

_CACHED_VALUE_TYPES = ("controlledValue", "keyword", "ontology", "ontologyOrValue")


class ValueCountsUpdater:
    """Keeps the field value counts cache in OpenSearchBeaconService up to date.

    The cache is used to speed up repeated filtering terms ``/values`` and
    ``/suggestions`` requests. The set of valid `/values`` and ``/suggestions``
    requests is known for any domain. A value count is asked for all filtering
    terms, optionally narrowed by scope and qualifiers. This service enumerates
    these combinations, fills the cache at startup, and refills it when
    new documents are indexed in OpenSearch.
    """

    def __init__(
        self,
        beacon_service: OpenSearchBeaconService,
        refresh_interval: float = 300.0,
    ) -> None:
        self._beacon_service = beacon_service
        self._poller = UpdatedPoller(
            "value counts",
            lambda: self._max_document_synced_at(),
            lambda: self.refresh(),
            refresh_interval,
        )

    def _value_count_keys(self) -> Iterator[ValueCountsKey]:
        """Yield every valid value counts key."""

        service = self._beacon_service
        for term in service.filtering_terms:
            if term.type not in _CACHED_VALUE_TYPES:
                continue
            qualifier_values = list(self._valid_qualifier_values(term.group))
            for scope in (None, *term.scopes):
                for qualifiers in qualifier_values:
                    yield ValueCountsKey.of(term.id, scope, qualifiers)

    def _valid_qualifier_values(
        self, group: str | None
    ) -> Iterator[dict[str, list[str]]]:
        """Yield every valid qualifier clause."""
        yield {}  # Requests without qualifiers.
        for qualifier in self._beacon_service.filtering_qualifiers:
            if group is None or group not in qualifier.groups:
                continue  # No group or the qualifier does not apply to it.
            # Requests with qualifier values.
            for value in qualifier.values:
                yield {qualifier.id: [value]}

    async def refresh(self) -> None:
        """Refresh the cached value counts.

        The poller does not call this until a document has been synced.

        Every key is counted concurrently. One key count failing will
        not affect the others.
        """
        self._beacon_service.clear_value_counts()
        keys: Sequence[ValueCountsKey] = list(self._value_count_keys())
        await asyncio.gather(*(self._refresh_key(key) for key in keys))
        logger.info("Refreshed value counts")

    async def _refresh_key(self, key: ValueCountsKey) -> None:
        try:
            await self._beacon_service.refresh_value_counts(key)
        except Exception:
            logger.exception("Failed to cache value counts for %s.", key)

    async def _max_document_synced_at(self) -> datetime | None:
        async with get_cursor() as cur:
            return await max_synced_at(cur)

    async def start(self) -> None:
        """Start the background task that refreshes the value count cache."""
        await self._poller.start()

    def stop(self) -> None:
        """Stop the background task that refreshes the value count cache."""
        self._poller.stop()
