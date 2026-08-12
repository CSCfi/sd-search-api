"""Integration tests for the server's connection pool."""

import os
from contextlib import AsyncExitStack

import pytest
import pytest_asyncio
from psycopg_pool import PoolTimeout

from search_api.conf import database_config
from search_api.database.repository import (
    close_pool,
    get_connection,
    open_pool,
)

os.environ.setdefault("POSTGRES_DB", os.environ["BP_POSTGRES_DB"])
os.environ.setdefault("POSTGRES_PORT", os.environ["BP_POSTGRES_PORT"])


@pytest_asyncio.fixture
async def pool(monkeypatch):
    """Open the pool, and close it."""
    monkeypatch.setenv("POSTGRES_POOL_TIMEOUT", "0.5")
    await open_pool()
    try:
        yield
    finally:
        await close_pool()


@pytest.mark.asyncio
async def test_max_pool_connection(pool):
    """Open one more connection than the pool supports."""
    async with AsyncExitStack() as held:
        # Get every connection in the pool.
        for _ in range(database_config().POSTGRES_POOL_MAX_SIZE):
            conn = await held.enter_async_context(get_connection())
            # Check that each connection works.
            cur = await conn.execute("SELECT 1")
            assert await cur.fetchone() == (1,)

        # Raises because the pool has no more connections.
        with pytest.raises(PoolTimeout):
            async with get_connection():
                pass

    # Leaving the stack returns the connections to the pool, which the fixture closes.
