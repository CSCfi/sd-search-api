"""Database connections.

The server pools connections. The pool is created by `open_pool` and
removed by `close_pool`. These are called by the servers lifespan.

If the pool is not open, connections are made directly.
"""

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

from psycopg import AsyncConnection, AsyncCursor
from psycopg.rows import tuple_row
from psycopg_pool import AsyncConnectionPool

from search_api.conf import database_admin_config, database_config

logger = logging.getLogger(__name__)

_pool: AsyncConnectionPool[AsyncConnection] | None = None


def _connect_shared_kwargs() -> dict[str, Any]:
    """Return the connection arguments shared by both users."""
    cfg = database_config()
    return {
        "host": cfg.POSTGRES_HOST,
        "port": cfg.POSTGRES_PORT,
        "dbname": cfg.POSTGRES_DB,
        "autocommit": True,
    }


def _connect_kwargs() -> dict[str, Any]:
    """Return the connection arguments for the user who uses the tables."""
    cfg = database_config()
    return {
        **_connect_shared_kwargs(),
        "user": cfg.POSTGRES_USER,
        "password": cfg.POSTGRES_PASSWORD.get_secret_value(),
    }


def _connect_admin_kwargs() -> dict[str, Any]:
    """Return the connection arguments for the user who owns the tables."""
    admin = database_admin_config()
    return {
        **_connect_shared_kwargs(),
        "user": admin.POSTGRES_ADMIN_USER,
        "password": admin.POSTGRES_ADMIN_PASSWORD.get_secret_value(),
    }


def _reconnect_failed(pool: AsyncConnectionPool[AsyncConnection]) -> None:
    """Report a pool that has stopped trying to reconnect.

    The pool retries on its own and keeps serving whatever connections it still has, so
    this is the only sign that it has run out of them for longer than
    `reconnect_timeout`.
    """
    logger.error(
        "Database pool '%s' failed to reconnect; %d connection(s) in the pool.",
        pool.name,
        pool.get_stats().get("pool_size", 0),
    )


async def open_pool() -> None:
    """Open the connection pool, if it is not open already."""
    global _pool
    if _pool is not None:
        return

    cfg = database_config()
    pool: AsyncConnectionPool[AsyncConnection] = AsyncConnectionPool(
        kwargs=_connect_kwargs(),
        min_size=cfg.POSTGRES_POOL_MIN_SIZE,
        max_size=cfg.POSTGRES_POOL_MAX_SIZE,
        # A connection is replaced once it reaches this age even while it is working.
        max_lifetime=cfg.POSTGRES_POOL_MAX_LIFETIME,
        # How long a caller waits when every connection is in use.
        timeout=cfg.POSTGRES_POOL_TIMEOUT,
        # Probe every connection on the way out of the pool. Failed connections are
        # discarded and replaced.
        check=AsyncConnectionPool.check_connection,
        reconnect_failed=_reconnect_failed,
        name="search-api",
        open=False,
    )
    # A database that is not up does not stop the server from starting. The
    # /health reports if the database is available.
    await pool.open(wait=False)
    _pool = pool
    logger.info(
        "Opened the database pool (min %d, max %d connections).",
        cfg.POSTGRES_POOL_MIN_SIZE,
        cfg.POSTGRES_POOL_MAX_SIZE,
    )


async def close_pool() -> None:
    """Close the connection pool, if one is open."""
    global _pool
    pool, _pool = _pool, None
    if pool is not None:
        await pool.close()
        logger.info("Closed the database pool.")


@asynccontextmanager
async def get_connection() -> AsyncGenerator[AsyncConnection]:
    """
    Get a database connection, from the pool if one is open.

    :return: a database connection, returned to the pool or closed on exit.
    """
    pool = _pool
    if pool is not None:
        async with pool.connection() as con:
            yield con
        return

    con = await AsyncConnection.connect(**_connect_kwargs())
    try:
        yield con
    finally:
        await con.close()


@asynccontextmanager
async def get_cursor() -> AsyncGenerator[AsyncCursor]:
    """
    Get a new database cursor that auto-commits.

    :return: a new database cursor that auto-commits.
    """
    async with get_connection() as con, con.cursor(row_factory=tuple_row) as cur:
        yield cur


@asynccontextmanager
async def get_admin_cursor() -> AsyncGenerator[AsyncCursor]:
    """
    Get a new database cursor that auto-commits for the user that owns the tables.

    The connection is never pooled.

    :return: a new database cursor that auto-commits for the user that owns the tables.
    """
    con = await AsyncConnection.connect(**_connect_admin_kwargs())
    try:
        async with con.cursor(row_factory=tuple_row) as cur:
            yield cur
    finally:
        await con.close()


@asynccontextmanager
async def get_transaction_cursor() -> AsyncGenerator[AsyncCursor]:
    """
    Get a new database cursor in a transaction.

    :return: a new database cursor in a transaction.
    """
    async with (
        get_connection() as con,
        con.transaction(),
        con.cursor(row_factory=tuple_row) as cur,
    ):
        yield cur


async def is_healthy() -> bool:
    """Return True if the database answers a query."""
    try:
        async with get_cursor() as cur:
            await cur.execute("SELECT 1")
            return await cur.fetchone() is not None
    except Exception:
        logger.exception("Database health check failed.")
        return False
