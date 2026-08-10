import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from psycopg import AsyncConnection, AsyncCursor
from psycopg.rows import tuple_row

from search_api.conf import database_config

logger = logging.getLogger(__name__)


@asynccontextmanager
async def get_connection() -> AsyncGenerator[AsyncConnection, None]:
    """
    Get a new database connection.

    :return: a new database connection.
    """
    cfg = database_config()
    conn = await AsyncConnection.connect(
        host=cfg.POSTGRES_HOST,
        port=cfg.POSTGRES_PORT,
        dbname=cfg.POSTGRES_DB,
        user=cfg.POSTGRES_USER,
        password=cfg.POSTGRES_PASSWORD,
        autocommit=True,
    )
    try:
        yield conn
    finally:
        await conn.close()


@asynccontextmanager
async def get_cursor() -> AsyncGenerator[AsyncCursor, None]:
    """
    Get a new database cursor.

    :return: a new database cursor.
    """
    async with get_connection() as con:
        async with con.cursor(row_factory=tuple_row) as cur:
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
