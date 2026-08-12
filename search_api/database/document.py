"""The document table."""

from collections.abc import AsyncIterator, Sequence
from datetime import datetime
from typing import Any

from psycopg import AsyncCursor
from psycopg.types.json import Json

DOCUMENT_TABLE = "document"


async def upsert_document(
    cur: AsyncCursor,
    doc_id: str,
    payload: dict[str, Any],
    modified_at: datetime | None,
) -> None:
    """Insert or replace a document, marking it unsynced."""
    await cur.execute(
        f"""
        INSERT INTO {DOCUMENT_TABLE} (id, payload, modified_at, synced_at)
        VALUES (%s, %s, %s, NULL)
        ON CONFLICT (id) DO UPDATE
        SET payload = EXCLUDED.payload,
            modified_at = EXCLUDED.modified_at,
            synced_at = NULL
        """,
        (doc_id, Json(payload), modified_at),
    )


async def get_document(cur: AsyncCursor, doc_id: str) -> dict[str, Any] | None:
    """Return a document payload, or None if not present."""
    await cur.execute(
        f"SELECT payload FROM {DOCUMENT_TABLE} WHERE id = %s",
        (doc_id,),
    )
    row = await cur.fetchone()
    return row[0] if row else None


async def get_modified_at(cur: AsyncCursor, doc_id: str) -> datetime | None:
    """Return a document's stored modified_at, or None if not present."""
    await cur.execute(
        f"SELECT modified_at FROM {DOCUMENT_TABLE} WHERE id = %s",
        (doc_id,),
    )
    row = await cur.fetchone()
    return row[0] if row else None


async def max_synced_at(cur: AsyncCursor) -> datetime | None:
    """Return when a document was last synced to the search index."""
    await cur.execute(f"SELECT max(synced_at) FROM {DOCUMENT_TABLE}")
    row = await cur.fetchone()
    return row[0] if row else None


async def iter_unsynced(
    cur: AsyncCursor, doc_id: str | None = None
) -> AsyncIterator[tuple[str, dict[str, Any]]]:
    """Yield (id, payload) for unsynced documents optionally filtered to one id."""
    query = f"SELECT id, payload FROM {DOCUMENT_TABLE} WHERE synced_at IS NULL"
    params: list[Any] = []
    if doc_id is not None:
        query += " AND id = %s"
        params.append(doc_id)
    await cur.execute(query, params)
    async for row in cur:
        yield row[0], row[1]


async def mark_synced(cur: AsyncCursor, ids: Sequence[str]) -> None:
    """Mark documents as synced to OpenSearch."""
    await cur.execute(
        f"UPDATE {DOCUMENT_TABLE} SET synced_at = now() WHERE id = ANY(%s)",
        (list(ids),),
    )


async def unsynced_count(cur: AsyncCursor) -> int:
    """Return the number of unsynced documents."""
    await cur.execute(
        f"SELECT COUNT(1) FROM {DOCUMENT_TABLE} WHERE synced_at IS NULL",
    )
    row = await cur.fetchone()
    return row[0] if row else 0


async def count_documents(cur: AsyncCursor) -> int:
    """Return the total number of documents."""
    await cur.execute(f"SELECT COUNT(1) FROM {DOCUMENT_TABLE}")
    row = await cur.fetchone()
    return row[0] if row else 0


async def reset_synced_at(cur: AsyncCursor) -> int:
    """Reset every document to pending sync and return the number of documents."""
    await cur.execute(f"UPDATE {DOCUMENT_TABLE} SET synced_at = NULL")
    return cur.rowcount


async def delete_all_documents(cur: AsyncCursor) -> int:
    """Delete all documents from the database and return the number of deleted documents."""
    await cur.execute(f"DELETE FROM {DOCUMENT_TABLE}")
    return cur.rowcount
