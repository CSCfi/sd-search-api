import os
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from psycopg import AsyncCursor

from search_api.database.document import (
    count_documents,
    delete_document,
    delete_documents_by_set,
    delete_dropped_documents,
    get_document,
    get_document_ids_by_sets,
    get_modified_at,
    iter_unsynced,
    mark_synced,
    pending_by_scope,
    unsynced_count,
    upsert_document,
)
from search_api.database.models import UnsyncedDocument
from search_api.database.repository import get_connection

os.environ.setdefault("POSTGRES_DB", os.environ["BP_POSTGRES_DB"])
os.environ.setdefault("POSTGRES_PORT", os.environ["BP_POSTGRES_PORT"])


_OLD_MODIFIED_AT = datetime(2025, 1, 1, tzinfo=UTC)
_CURRENT_MODIFIED_AT = datetime(2026, 1, 1, tzinfo=UTC)
_NEW_MODIFIED_AT = datetime(2027, 1, 1, tzinfo=UTC)


def _doc_id() -> str:
    return f"test-{uuid.uuid4()}"


@pytest_asyncio.fixture
async def cur() -> AsyncIterator[AsyncCursor]:
    """A cursor that rollbacks."""
    async with (
        get_connection() as conn,
        conn.transaction(force_rollback=True),
        conn.cursor() as cursor,
    ):
        yield cursor


async def _unsynced(cur, doc_ids: list[str]) -> list[UnsyncedDocument]:
    """Return the documents pending sync, as a sync reads them."""
    return [doc async for doc in iter_unsynced(cur) if doc.id in doc_ids]


async def _synced_at(cur, doc_id: str) -> datetime | None:
    """Return the synced_at the database stamped on a document."""
    await cur.execute("SELECT synced_at FROM document WHERE id = %s", (doc_id,))
    row = await cur.fetchone()
    return row[0] if row else None


@pytest.mark.asyncio
async def test_upsert_document():
    doc_id = _doc_id()
    async with get_connection() as conn, conn.cursor() as cur:
        await upsert_document(cur, doc_id, {"v": 1}, _OLD_MODIFIED_AT, None)

        # Simulate a prior sync.
        await cur.execute(
            "UPDATE document SET synced_at = now() WHERE id = %s", (doc_id,)
        )

        # Upsert with new payload should overwrite and clear synced_at.
        await upsert_document(cur, doc_id, {"v": 2}, _OLD_MODIFIED_AT, None)

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
    async with get_connection() as conn, conn.cursor() as cur:
        await upsert_document(cur, id_a, {"x": "a"}, _OLD_MODIFIED_AT, None)
        await upsert_document(cur, id_b, {"x": "b"}, _OLD_MODIFIED_AT, None)

        assert await unsynced_count(cur) >= 2

        unsynced = {doc.id async for doc in iter_unsynced(cur)}
        assert id_a in unsynced
        assert id_b in unsynced

        count_before = await unsynced_count(cur)
        await mark_synced(cur, await _unsynced(cur, [id_a, id_b]))

        assert await unsynced_count(cur) == count_before - 2
        unsynced_after = {doc.id async for doc in iter_unsynced(cur)}
        assert id_a not in unsynced_after
        assert id_b not in unsynced_after

        await cur.execute("DELETE FROM document WHERE id = ANY(%s)", ([id_a, id_b],))


@pytest.mark.asyncio
async def test_mark_synced_skips_document_changed_since_read():
    """A document stored again during a sync stays pending.

    The sync indexed the version it read, not the new one, so the next sync must
    index the new one. Each write is its own transaction, as in a load: writes in one
    transaction would share a version.
    """
    doc_id = _doc_id()
    async with get_connection() as conn, conn.cursor() as cur:
        await upsert_document(cur, doc_id, {"v": 1}, _OLD_MODIFIED_AT, None)
        read_documents = await _unsynced(cur, [doc_id])
        await upsert_document(cur, doc_id, {"v": 2}, _CURRENT_MODIFIED_AT, None)

        assert await mark_synced(cur, read_documents) == 0
        assert await _synced_at(cur, doc_id) is None

        assert await mark_synced(cur, await _unsynced(cur, [doc_id])) == 1
        assert await _synced_at(cur, doc_id) is not None

        await cur.execute("DELETE FROM document WHERE id = %s", (doc_id,))


@pytest.mark.asyncio
async def test_get_modified_at():
    doc_id = _doc_id()
    modified_at = datetime(2024, 6, 1, tzinfo=UTC)
    async with get_connection() as conn, conn.cursor() as cur:
        await upsert_document(cur, doc_id, {}, modified_at, None)
        result = await get_modified_at(cur, doc_id)
        assert result == modified_at

        await cur.execute("DELETE FROM document WHERE id = %s", (doc_id,))


@pytest.mark.asyncio
async def test_get_modified_at_returns_none_when_not_stored(cur):
    assert await get_modified_at(cur, _doc_id()) is None


@pytest.mark.asyncio
async def test_count_documents():
    doc_id = _doc_id()
    count_before = await count_documents()
    async with get_connection() as conn, conn.cursor() as cur:
        await upsert_document(cur, doc_id, {}, _OLD_MODIFIED_AT, None)
        assert await count_documents() == count_before + 1
        await cur.execute("DELETE FROM document WHERE id = %s", (doc_id,))


@pytest.mark.asyncio
async def test_pending_by_scope():
    id_clinical, id_non_clinical = _doc_id(), _doc_id()
    before_pending = await pending_by_scope()

    async with get_connection() as conn, conn.cursor() as cur:
        await upsert_document(
            cur, id_clinical, {"scope": "clinical"}, _OLD_MODIFIED_AT, None
        )
        await upsert_document(
            cur, id_non_clinical, {"scope": "non_clinical"}, _OLD_MODIFIED_AT, None
        )

        after_pending = await pending_by_scope()
        assert after_pending["clinical"] == before_pending.get("clinical", 0) + 1
        assert (
            after_pending["non_clinical"] == before_pending.get("non_clinical", 0) + 1
        )

        # A scope with nothing pending has no row to group.
        await mark_synced(cur, await _unsynced(cur, [id_clinical]))
        synced_pending = await pending_by_scope()
        assert synced_pending.get("clinical", 0) == before_pending.get("clinical", 0)
        assert synced_pending["non_clinical"] == after_pending["non_clinical"]

        await cur.execute(
            "DELETE FROM document WHERE id = ANY(%s)",
            ([id_clinical, id_non_clinical],),
        )


# Deletion
#


async def _stored(cur: AsyncCursor, doc_id: str) -> bool:
    return await get_document(cur, doc_id) is not None


@pytest.mark.asyncio
async def test_delete_document(cur):
    doc_id, other_id = _doc_id(), _doc_id()
    await upsert_document(cur, doc_id, {}, _OLD_MODIFIED_AT, None)
    await upsert_document(cur, other_id, {}, _OLD_MODIFIED_AT, None)

    assert await delete_document(cur, doc_id) is True
    assert await delete_document(cur, doc_id) is False

    assert not await _stored(cur, doc_id)
    assert await _stored(cur, other_id)


@pytest.mark.asyncio
async def test_get_document_ids_by_sets(cur):
    set_id, other_set_id = _doc_id(), _doc_id()
    id_1, id_2, id_3 = _doc_id(), _doc_id(), _doc_id()
    await upsert_document(cur, id_1, {}, _OLD_MODIFIED_AT, set_id)
    await upsert_document(cur, id_2, {}, _OLD_MODIFIED_AT, set_id)
    await upsert_document(cur, id_3, {}, _OLD_MODIFIED_AT, other_set_id)

    assert sorted(await get_document_ids_by_sets(cur, [set_id])) == sorted([id_1, id_2])


@pytest.mark.asyncio
async def test_delete_documents_by_set(cur):
    set_id, other_set_id = _doc_id(), _doc_id()
    id_1, id_2, id_3 = _doc_id(), _doc_id(), _doc_id()
    await upsert_document(cur, id_1, {}, _OLD_MODIFIED_AT, set_id)
    await upsert_document(cur, id_2, {}, _OLD_MODIFIED_AT, set_id)
    await upsert_document(cur, id_3, {}, _OLD_MODIFIED_AT, other_set_id)

    deleted = await delete_documents_by_set(cur, set_id)

    assert sorted(deleted) == sorted([id_1, id_2])
    assert await _stored(cur, id_3)


@pytest.mark.asyncio
async def test_delete_dropped_documents_deletes_old_document(cur):
    set_id, kept_doc_id, dropped_doc_id = _doc_id(), _doc_id(), _doc_id()
    await upsert_document(cur, kept_doc_id, {}, _CURRENT_MODIFIED_AT, set_id)
    await upsert_document(cur, dropped_doc_id, {}, _OLD_MODIFIED_AT, set_id)

    deleted = await delete_dropped_documents(
        cur, set_id, [kept_doc_id], _CURRENT_MODIFIED_AT
    )

    assert deleted == [dropped_doc_id]
    assert await _stored(cur, kept_doc_id)


@pytest.mark.asyncio
async def test_delete_dropped_documents_keeps_current_ids(cur):
    """A current document is kept even when older."""
    set_id, kept_doc_id = _doc_id(), _doc_id()
    await upsert_document(cur, kept_doc_id, {}, _OLD_MODIFIED_AT, set_id)

    assert (
        await delete_dropped_documents(cur, set_id, [kept_doc_id], _CURRENT_MODIFIED_AT)
        == []
    )
    assert await _stored(cur, kept_doc_id)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "modified_at", [_CURRENT_MODIFIED_AT, _NEW_MODIFIED_AT], ids=["same", "newer"]
)
async def test_delete_dropped_documents_keeps_documents_not_older(cur, modified_at):
    set_id, doc_id = _doc_id(), _doc_id()
    await upsert_document(cur, doc_id, {}, modified_at, set_id)

    assert await delete_dropped_documents(cur, set_id, [], _CURRENT_MODIFIED_AT) == []
    assert await _stored(cur, doc_id)


@pytest.mark.asyncio
async def test_delete_dropped_documents_keeps_other_sets(cur):
    set_id, other_set_id, doc_id = _doc_id(), _doc_id(), _doc_id()
    await upsert_document(cur, doc_id, {}, _OLD_MODIFIED_AT, other_set_id)

    assert await delete_dropped_documents(cur, set_id, [], _CURRENT_MODIFIED_AT) == []
    assert await _stored(cur, doc_id)
