"""The document table.

Functions that participate in a transaction require a cursor, the
others do not.
"""

from collections.abc import AsyncGenerator, Sequence
from datetime import datetime
from typing import Any

from psycopg import AsyncCursor
from psycopg.rows import tuple_row
from psycopg.types.json import Json

from search_api.database.models import UnsyncedDocument
from search_api.database.repository import get_cursor

DOCUMENT_TABLE = "document"


async def upsert_document(
    cur: AsyncCursor,
    doc_id: str,
    payload: dict[str, Any],
    modified_at: datetime,
    set_id: str | None,
) -> None:
    """Insert or replace a document, marking it unsynced."""
    await cur.execute(
        f"""
        INSERT INTO {DOCUMENT_TABLE} (id, payload, modified_at, synced_at, set_id)
        VALUES (%s, %s, %s, NULL, %s)
        ON CONFLICT (id) DO UPDATE
        SET payload = EXCLUDED.payload,
            modified_at = EXCLUDED.modified_at,
            synced_at = NULL,
            set_id = EXCLUDED.set_id
        """,
        (doc_id, Json(payload), modified_at, set_id),
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
    """Return a document's stored modified_at, or None if the document is not stored."""
    await cur.execute(
        f"SELECT modified_at FROM {DOCUMENT_TABLE} WHERE id = %s",
        (doc_id,),
    )
    row = await cur.fetchone()
    return row[0] if row else None


async def max_synced_at() -> datetime | None:
    """Return when a document was last synced to the search index."""
    async with get_cursor() as cur:
        await cur.execute(f"SELECT max(synced_at) FROM {DOCUMENT_TABLE}")
        row = await cur.fetchone()
    return row[0] if row else None


async def iter_unsynced(
    cur: AsyncCursor, doc_id: str | None = None
) -> AsyncGenerator[UnsyncedDocument]:
    """Yield the unsynced documents, optionally filtered to one id.

    Includes row version for optimistic locking in mark_synced.
    """
    query = (
        f"SELECT id, payload, xmin::text FROM {DOCUMENT_TABLE} WHERE synced_at IS NULL"
    )
    params: list[Any] = []
    if doc_id is not None:
        query += " AND id = %s"
        params.append(doc_id)
    # Many documents may be pending. A normal cursor would load all their
    # payloads into memory at once. A server-side cursor reads them in batches
    # instead. It needs WITH HOLD because the connection auto-commits.
    async with cur.connection.cursor(
        name="unsynced_documents", row_factory=tuple_row, withhold=True
    ) as server_cur:
        await server_cur.execute(query, params)
        async for row in server_cur:
            yield UnsyncedDocument(id=row[0], payload=row[1], version=row[2])


async def mark_synced(cur: AsyncCursor, documents: Sequence[UnsyncedDocument]) -> int:
    """Mark documents as synced to OpenSearch.

    Uses optimistic locking to only mark those document rows synced that were
    not updated since selecting rows for syncing.

    :param documents: The documents to be synced from iter_unsynced.
    :return: The number of documents marked synced.
    """
    await cur.executemany(
        f"UPDATE {DOCUMENT_TABLE} SET synced_at = now() "
        f"WHERE id = %s AND xmin::text = %s",
        [(doc.id, doc.version) for doc in documents],
    )
    return cur.rowcount


async def unsynced_count(cur: AsyncCursor) -> int:
    """Return the number of unsynced documents."""
    await cur.execute(
        f"SELECT COUNT(1) FROM {DOCUMENT_TABLE} WHERE synced_at IS NULL",
    )
    row = await cur.fetchone()
    return row[0] if row else 0


async def pending_by_scope() -> dict[str | None, int]:
    """Return the number of documents pending sync, by scope."""
    async with get_cursor() as cur:
        await cur.execute(
            f"SELECT payload->>'scope', COUNT(1) FROM {DOCUMENT_TABLE} "
            f"WHERE synced_at IS NULL GROUP BY 1"
        )
        return {scope: count for scope, count in await cur.fetchall()}


async def count_documents() -> int:
    """Return the total number of documents."""
    async with get_cursor() as cur:
        await cur.execute(f"SELECT COUNT(1) FROM {DOCUMENT_TABLE}")
        row = await cur.fetchone()
    return row[0] if row else 0


async def get_document_ids_by_sets(
    cur: AsyncCursor, set_ids: Sequence[str]
) -> list[str]:
    """Return the ids of the document sets' documents."""
    await cur.execute(
        f"SELECT id FROM {DOCUMENT_TABLE} WHERE set_id = ANY(%s)",
        (list(set_ids),),
    )
    return [row[0] for row in await cur.fetchall()]


async def reset_synced_at(cur: AsyncCursor) -> int:
    """Reset every document to pending sync and return the number of documents."""
    await cur.execute(f"UPDATE {DOCUMENT_TABLE} SET synced_at = NULL")
    return cur.rowcount


async def reset_synced_at_by_set(cur: AsyncCursor, set_id: str) -> int:
    """Reset the document set's documents to pending sync.

    :return: The number of documents reset.
    """
    await cur.execute(
        f"UPDATE {DOCUMENT_TABLE} SET synced_at = NULL WHERE set_id = %s",
        (set_id,),
    )
    return cur.rowcount


async def delete_document(cur: AsyncCursor, doc_id: str) -> bool:
    """Delete a document and return True if the document existed."""
    await cur.execute(f"DELETE FROM {DOCUMENT_TABLE} WHERE id = %s", (doc_id,))
    return cur.rowcount > 0


async def delete_documents_by_set(cur: AsyncCursor, set_id: str) -> list[str]:
    """Delete the document set's documents and return their ids."""
    await cur.execute(
        f"DELETE FROM {DOCUMENT_TABLE} WHERE set_id = %s RETURNING id",
        (set_id,),
    )
    return [row[0] for row in await cur.fetchall()]


async def delete_dropped_documents(
    cur: AsyncCursor,
    set_id: str,
    current_ids: Sequence[str],
    current_modified_at: datetime,
) -> list[str]:
    """Delete the documents no longer part of a newer document set version.

    :param cur: The database cursor.
    :param set_id: The document set id.
    :param current_ids: The ids of the documents in the current document set version
        being loaded.
    :param current_modified_at: When the current document set version was modified.
        A stored document modified before this is from an older document set version
        and is deleted unless it is included in ``current_ids``.
    :return: The ids of the deleted documents.
    """
    await cur.execute(
        f"DELETE FROM {DOCUMENT_TABLE} "
        f"WHERE set_id = %s AND NOT id = ANY(%s) AND modified_at < %s "
        f"RETURNING id",
        (set_id, list(current_ids), current_modified_at),
    )
    return [row[0] for row in await cur.fetchall()]


async def delete_all_documents(cur: AsyncCursor) -> int:
    """Delete all documents from the database and return the number of deleted documents."""
    await cur.execute(f"DELETE FROM {DOCUMENT_TABLE}")
    return cur.rowcount
