"""UpdatedPoller: when it refreshes, and when it leaves things alone."""

import asyncio
from datetime import datetime, timezone

import pytest

from search_api.services.poller import UpdatedPoller

_T1 = datetime(2026, 1, 1, tzinfo=timezone.utc)
_T2 = datetime(2026, 2, 1, tzinfo=timezone.utc)


class _Store:
    """A store a cache is filled from that calculates the number of refreshes.

    Tests change ``updated_at`` to indicate that the refresh() should be called.
    """

    def __init__(self, updated_at=_T1) -> None:
        self.updated_at = updated_at
        self.refreshes = 0
        self.fail = False

    async def read_updated_at(self):
        return self.updated_at

    async def refresh(self) -> None:
        self.refreshes += 1
        if self.fail:
            raise RuntimeError("refresh failed")


def _poller(store: _Store) -> UpdatedPoller:
    return UpdatedPoller(
        "test cache", store.read_updated_at, store.refresh, interval=0.01
    )


async def _poll_a_few_times(poller: UpdatedPoller) -> None:
    try:
        await asyncio.sleep(0.05)
    finally:
        poller.stop()


@pytest.mark.asyncio
async def test_start_refreshes_once():
    store = _Store()
    poller = _poller(store)

    await poller.start()
    poller.stop()

    assert store.refreshes == 1


@pytest.mark.asyncio
async def test_no_refresh_if_unchanged_updated_at():
    store = _Store()
    poller = _poller(store)
    await poller.start()

    await _poll_a_few_times(poller)

    assert store.refreshes == 1  # only the one from start


@pytest.mark.asyncio
async def test_refresh_if_changed_updated_at():
    store = _Store()
    poller = _poller(store)
    await poller.start()

    store.updated_at = _T2
    await _poll_a_few_times(poller)

    assert store.refreshes == 2  # the one from start and one from updated_at change


@pytest.mark.asyncio
async def test_stop_cancels_the_task():
    store = _Store()
    poller = _poller(store)
    await poller.start()
    task = poller._task

    poller.stop()
    await asyncio.sleep(0)

    assert task is not None and task.done()
    assert poller._task is None


@pytest.mark.asyncio
async def test_start_twice_leaves_one_task():
    store = _Store()
    poller = _poller(store)

    await poller.start()
    first = poller._task
    await poller.start()
    try:
        assert poller._task is first
    finally:
        poller.stop()
