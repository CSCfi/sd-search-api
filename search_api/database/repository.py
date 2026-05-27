from contextlib import asynccontextmanager
from typing import AsyncGenerator

from psycopg import AsyncConnection, AsyncCursor
from psycopg.rows import tuple_row

from search_api.conf import common_config


@asynccontextmanager
async def get_connection() -> AsyncGenerator[AsyncConnection, None]:
    """
    Get a new database connection.

    :return: a new database connection.
    """
    cfg = common_config()
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
