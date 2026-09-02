import os

import pytest
import pytest_asyncio

from search_api.database.load import (
    LOAD_HISTORY_TABLE,
    LOAD_TABLE,
    delete_load_marker,
    insert_load_history,
    read_load_marker,
    write_load_marker,
)
from search_api.database.repository import get_cursor

os.environ.setdefault("POSTGRES_DB", os.environ["BP_POSTGRES_DB"])
os.environ.setdefault("POSTGRES_PORT", os.environ["BP_POSTGRES_PORT"])

MARKER = "2026-01-02T12:00:00+00:00"
LATER = "2026-01-03T12:00:00+00:00"


@pytest_asyncio.fixture
async def empty_load():
    async def clear():
        async with get_cursor() as cur:
            await cur.execute(f"DELETE FROM {LOAD_TABLE}")
            await cur.execute(f"DELETE FROM {LOAD_HISTORY_TABLE}")

    await clear()
    yield
    await clear()


async def _read_markers(table: str) -> list[tuple]:
    async with get_cursor() as cur:
        await cur.execute(f"SELECT marker FROM {table} ORDER BY 1")
        return await cur.fetchall()


@pytest.mark.asyncio
async def test_read_load_marker(empty_load):
    assert await read_load_marker() is None
    await write_load_marker(MARKER)
    assert await read_load_marker() == MARKER


@pytest.mark.asyncio
async def test_write_load_marker(empty_load):
    await write_load_marker(MARKER)
    assert await read_load_marker() == MARKER

    await write_load_marker(LATER)
    assert await read_load_marker() == LATER

    assert await _read_markers(LOAD_TABLE) == [(LATER,)]


@pytest.mark.asyncio
async def test_delete_marker(empty_load):
    await write_load_marker(MARKER)
    assert await read_load_marker() == MARKER
    await delete_load_marker()
    assert await read_load_marker() is None


@pytest.mark.asyncio
async def test_load_history(empty_load):
    await insert_load_history(MARKER)
    assert await _read_markers(LOAD_HISTORY_TABLE) == [(MARKER,)]
    await insert_load_history(LATER)
    assert await _read_markers(LOAD_HISTORY_TABLE) == [(MARKER,), (LATER,)]

    await delete_load_marker()
    assert await _read_markers(LOAD_HISTORY_TABLE) == [(MARKER,), (LATER,)]
