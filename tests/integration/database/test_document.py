import os
import uuid
from datetime import datetime, timezone

import pytest

from search_api.database.document import (
    count_documents,
    get_modified_at,
    iter_unsynced,
    mark_synced,
    pending_by_scope,
    unsynced_count,
    upsert_document,
)
from search_api.database.repository import get_connection

os.environ.setdefault("POSTGRES_DB", os.environ["BP_POSTGRES_DB"])
os.environ.setdefault("POSTGRES_PORT", os.environ["BP_POSTGRES_PORT"])


def _doc_id() -> str:
    return f"test-{uuid.uuid4()}"


async def _synced_at(cur, doc_id: str) -> datetime | None:
    """Return the synced_at the database stamped on a document."""
    await cur.execute("SELECT synced_at FROM document WHERE id = %s", (doc_id,))
    row = await cur.fetchone()
    return row[0] if row else None


@pytest.mark.asyncio
async def test_upsert_document():
    doc_id = _doc_id()
    async with get_connection() as conn:
        async with conn.cursor() as cur:
            await upsert_document(cur, doc_id, {"v": 1}, None)

            # Simulate a prior sync.
            await cur.execute(
                "UPDATE document SET synced_at = now() WHERE id = %s", (doc_id,)
            )

            # Upsert with new payload should overwrite and clear synced_at.
            await upsert_document(cur, doc_id, {"v": 2}, None)

            await cur.execute(
                "SELECT payload, synced_at FROM document WHERE id = %s", (doc_id,)
            )
            row = await cur.fetchone()
            assert row is not None
            assert row[0]["v"] == 2
            assert row[1] is None

            await cur.execute("DELETE FROM document WHERE id = %s", (doc_id,))


@pytest.mark.asyncio
async def test_sync():
    id_a, id_b = _doc_id(), _doc_id()
    async with get_connection() as conn:
        async with conn.cursor() as cur:
            await upsert_document(cur, id_a, {"x": "a"}, None)
            await upsert_document(cur, id_b, {"x": "b"}, None)

            assert await unsynced_count(cur) >= 2

            unsynced = {doc_id async for doc_id, _ in iter_unsynced(cur)}
            assert id_a in unsynced
            assert id_b in unsynced

            count_before = await unsynced_count(cur)
            await mark_synced(cur, [id_a, id_b])

            assert await unsynced_count(cur) == count_before - 2
            unsynced_after = {doc_id async for doc_id, _ in iter_unsynced(cur)}
            assert id_a not in unsynced_after
            assert id_b not in unsynced_after

            await cur.execute(
                "DELETE FROM document WHERE id = ANY(%s)", ([id_a, id_b],)
            )


@pytest.mark.asyncio
async def test_get_modified_at():
    doc_id = _doc_id()
    modified_at = datetime(2024, 6, 1, tzinfo=timezone.utc)
    async with get_connection() as conn:
        async with conn.cursor() as cur:
            await upsert_document(cur, doc_id, {}, modified_at)
            result = await get_modified_at(cur, doc_id)
            assert result == modified_at

            await cur.execute("DELETE FROM document WHERE id = %s", (doc_id,))


@pytest.mark.asyncio
async def test_get_modified_at_returns_none_when_not_set():
    doc_id = _doc_id()
    async with get_connection() as conn:
        async with conn.cursor() as cur:
            await upsert_document(cur, doc_id, {}, None)
            result = await get_modified_at(cur, doc_id)
            assert result is None

            await cur.execute("DELETE FROM document WHERE id = %s", (doc_id,))


@pytest.mark.asyncio
async def test_count_documents():
    doc_id = _doc_id()
    count_before = await count_documents()
    async with get_connection() as conn:
        async with conn.cursor() as cur:
            await upsert_document(cur, doc_id, {}, None)
            assert await count_documents() == count_before + 1
            await cur.execute("DELETE FROM document WHERE id = %s", (doc_id,))


@pytest.mark.asyncio
async def test_pending_by_scope():
    id_clinical, id_non_clinical = _doc_id(), _doc_id()
    before_pending = await pending_by_scope()

    async with get_connection() as conn:
        async with conn.cursor() as cur:
            await upsert_document(cur, id_clinical, {"scope": "clinical"}, None)
            await upsert_document(cur, id_non_clinical, {"scope": "non_clinical"}, None)

            after_pending = await pending_by_scope()
            assert after_pending["clinical"] == before_pending.get("clinical", 0) + 1
            assert (
                after_pending["non_clinical"]
                == before_pending.get("non_clinical", 0) + 1
            )

            # A scope with nothing pending has no row to group.
            await mark_synced(cur, [id_clinical])
            synced_pending = await pending_by_scope()
            assert synced_pending.get("clinical", 0) == before_pending.get(
                "clinical", 0
            )
            assert synced_pending["non_clinical"] == after_pending["non_clinical"]

            await cur.execute(
                "DELETE FROM document WHERE id = ANY(%s)",
                ([id_clinical, id_non_clinical],),
            )
