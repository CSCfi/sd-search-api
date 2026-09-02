"""The load and load_history tables."""

from search_api.database.repository import get_cursor

LOAD_TABLE = "load"
LOAD_HISTORY_TABLE = "load_history"


async def read_load_marker() -> str | None:
    """
    Return the incremental load position.

    :return: The incremental load position, or None if nothing has been loaded.
    """

    async with get_cursor() as cur:
        await cur.execute(f"SELECT marker FROM {LOAD_TABLE}")
        row = await cur.fetchone()
    return row[0] if row else None


async def write_load_marker(marker: str) -> None:
    """
    Save the incremental load position.

    :param marker: The incremental load position.
    """

    async with get_cursor() as cur:
        await cur.execute(
            f"INSERT INTO {LOAD_TABLE} (marker) VALUES (%s) "
            f"ON CONFLICT (id) DO UPDATE SET marker = EXCLUDED.marker, updated_at = now()",
            (marker,),
        )


async def delete_load_marker() -> None:
    """Delete incremental load position, so the next load starts over."""

    async with get_cursor() as cur:
        await cur.execute(f"DELETE FROM {LOAD_TABLE}")


async def insert_load_history(marker: str) -> None:
    """
    Save incremental load history.

    :param marker: The incremental load position.
    """

    async with get_cursor() as cur:
        await cur.execute(
            f"INSERT INTO {LOAD_HISTORY_TABLE} (marker) VALUES (%s)", (marker,)
        )
