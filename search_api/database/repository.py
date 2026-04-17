from contextlib import asynccontextmanager
from typing import AsyncGenerator

from psycopg import AsyncConnection, AsyncCursor
from psycopg.rows import tuple_row


@asynccontextmanager
async def get_connection() -> AsyncGenerator[AsyncConnection, None]:
    """
    Get a new database connection.

    :return: a new database connection.
    """
    # TODO(improve): read connection details from an environmental variable

    conn = await AsyncConnection.connect(
        host="localhost",
        dbname="sd_search",
        user="postgres",
        password="test",
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
