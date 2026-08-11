import asyncio
from datetime import datetime, timezone

import pytest

from search_api.api.bigpicture.models import (
    BP_FILTERING_QUALIFIERS,
    BP_FILTERING_SCOPES,
    BP_FILTERING_TERMS,
)
from search_api.api.bigpicture.opensearch import BigpictureOpenSearchBeaconService
from search_api.api.models import ValueCounts, ValueCountsKey
from search_api.services.value_counts import ValueCountsUpdater


class _MockBeaconService(BigpictureOpenSearchBeaconService):
    """Records which values the cache was asked to count, without using OpenSearch."""

    def __init__(self) -> None:
        super().__init__(
            client=None,  # type: ignore[arg-type]
            index_name="idx",
            filtering_terms=BP_FILTERING_TERMS,
            filtering_scopes=BP_FILTERING_SCOPES,
            filtering_qualifiers=BP_FILTERING_QUALIFIERS,
        )
        self.calls: list[ValueCountsKey] = []

    async def _count_values(self, key: ValueCountsKey) -> ValueCounts:
        self.calls.append(key)
        return ValueCounts(counts={})


def _service(
    **kwargs,
) -> tuple[ValueCountsUpdater, _MockBeaconService]:
    beacon = _MockBeaconService()
    return ValueCountsUpdater(beacon, **kwargs), beacon


# The filtering term types that report no values, so /values and /suggestions
# reject them. Named as the complement of what the updater enumerates, so a type
# added to either list has to be considered here too.
_TYPES_WITHOUT_VALUES = ("text", "iso8601Range", "integer")

# What Bigpicture's configuration currently amounts to. A canary: a field, scope
# or qualifier added to the configuration changes it.
_BP_KEY_COUNT = 62


def test_value_count_keys_are_every_valid_request():
    """Every field, in each of its scopes, under each qualifier value of its group.

    Built from Bigpicture's own configuration, so it restates the rule rather than
    proving it. What it catches is the implementation drifting away from the rule.
    """
    keys = set(_service()[0]._value_count_keys())

    expected = set()
    for term in BP_FILTERING_TERMS:
        if term.type in _TYPES_WITHOUT_VALUES:
            continue
        qualifiers: list[dict[str, list[str]]] = [{}]
        for qualifier in BP_FILTERING_QUALIFIERS:
            if term.nested_group in qualifier.groups:
                qualifiers += [{qualifier.id: [value]} for value in qualifier.values]
        for scope in (None, *term.scopes):
            for requested in qualifiers:
                expected.add(ValueCountsKey.of(term.id, scope, requested))

    assert keys == expected
    assert len(keys) == _BP_KEY_COUNT


@pytest.mark.asyncio
async def test_refresh_requests_every_key_once():
    service, beacon = _service()
    await service.refresh()

    assert len(beacon.calls) == len(list(service._value_count_keys()))
    assert len(set(beacon.calls)) == len(beacon.calls), "a key was repeated"


@pytest.mark.asyncio
async def test_the_cache_follows_what_has_been_synced(monkeypatch):
    """Nothing is counted before a load lands, and nothing is recounted after it."""
    synced_at: datetime | None = None
    service, beacon = _service(refresh_interval=0.01)
    monkeypatch.setattr(
        service, "_max_document_synced_at", lambda: _resolved(synced_at)
    )

    # Nothing to refresh yet (_max_document_synced_at is None).
    await service.start()
    try:
        await asyncio.sleep(0.05)
        assert beacon.calls == [], "counted an index no load has reached"

        # Changes to refresh (_max_document_synced_at is not None).
        synced_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
        await asyncio.sleep(0.05)
        assert beacon.calls, "did not fill after documents were synced"

        # Nothing new to refresh (_max_document_synced_at is unchanged).
        beacon.calls.clear()
        await asyncio.sleep(0.05)
        assert beacon.calls == [], "refilled while nothing was synced"
    finally:
        service.stop()


async def _resolved(value):
    return value
