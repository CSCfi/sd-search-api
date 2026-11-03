"""Integration tests for SyncService."""

import os
import re
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from search_api.api.bigpicture.models import BP_DATASET_ID_FIELD
from search_api.database.document import (
    DOCUMENT_TABLE,
    count_documents,
    get_document,
    upsert_document,
)
from search_api.database.document_log import (
    DOCUMENT_LOG_TABLE,
    read_document_logs,
    write_document_log,
)
from search_api.database.models import StoredDocumentLog
from search_api.database.repository import get_connection
from search_api.exceptions import SystemException
from search_api.services.sync import SyncService

os.environ.setdefault("POSTGRES_DB", os.environ["BP_POSTGRES_DB"])
os.environ.setdefault("POSTGRES_PORT", os.environ["BP_POSTGRES_PORT"])

_OLD_MODIFIED_AT = datetime(2025, 1, 1, tzinfo=UTC)
_CURRENT_MODIFIED_AT = datetime(2026, 1, 1, tzinfo=UTC)

_INDEX_DELETE_BY_FIELD = "search_api.services.sync.delete_opensearch_documents_by_field"


def _doc_id() -> str:
    return f"test-{uuid.uuid4()}"


@pytest.mark.asyncio
async def test_delete_all_documents(bp_opensearch_index, bp_opensearch_index_name):
    sync_service = SyncService(bp_opensearch_index_name)
    try:
        async with get_connection() as conn, conn.cursor() as cur:
            database_count_before = await count_documents()
            opensearch_count_before = (
                await sync_service.search.count(index=bp_opensearch_index_name)
            )["count"]

            # Add two documents to the database.
            doc_id_1 = _doc_id()
            doc_id_2 = _doc_id()
            await upsert_document(
                cur, doc_id_1, {"image_id": "sync-test-1"}, _OLD_MODIFIED_AT, None
            )
            await upsert_document(
                cur, doc_id_2, {"image_id": "sync-test-2"}, _OLD_MODIFIED_AT, None
            )
            # Sync the documents to OpenSearch index.
            await sync_service.sync_fields(cur, doc_id_1)
            await sync_service.sync_fields(cur, doc_id_2)
            await sync_service.search.indices.refresh(index=bp_opensearch_index_name)

            assert await count_documents() == database_count_before + 2
            opensearch_count_after_sync = (
                await sync_service.search.count(index=bp_opensearch_index_name)
            )["count"]
            assert opensearch_count_after_sync == opensearch_count_before + 2

            await sync_service.delete_all_documents(cur)

            assert await count_documents() == 0
            opensearch_count_after_delete = (
                await sync_service.search.count(index=bp_opensearch_index_name)
            )["count"]
            assert opensearch_count_after_delete == 0
    finally:
        await sync_service.search.close()


@pytest_asyncio.fixture
async def sync_service(bp_opensearch_index, bp_opensearch_index_name):
    service = SyncService(bp_opensearch_index_name, BP_DATASET_ID_FIELD)
    yield service
    await service.search.close()


@pytest_asyncio.fixture
async def doc_ids():
    """Three document ids, whose rows and log rows are deleted afterwards."""
    ids = [_doc_id() for _ in range(3)]
    yield ids
    async with get_connection() as conn, conn.cursor() as cur:
        await cur.execute(f"DELETE FROM {DOCUMENT_TABLE} WHERE id = ANY(%s)", (ids,))
        await cur.execute(
            f"DELETE FROM {DOCUMENT_LOG_TABLE} WHERE document_id = ANY(%s)", (ids,)
        )


async def _store_and_sync_bp(
    sync_service: SyncService,
    doc_id: str,
    set_id: str,
    modified_at: datetime = _OLD_MODIFIED_AT,
) -> None:
    """Store a Bigpicture document with a log row, then sync it."""
    await _store_bp(doc_id, set_id, modified_at)
    async with get_connection() as conn, conn.cursor() as cur:
        await write_document_log(
            cur,
            StoredDocumentLog(document_id=doc_id, severity="ERROR", message="Logged."),
        )
    async with get_connection() as conn, conn.cursor() as cur:
        await sync_service.sync_fields(cur, doc_id)


async def _store_bp(doc_id: str, set_id: str, modified_at: datetime) -> None:
    """Store a Bigpicture document pending sync."""
    async with get_connection() as conn, conn.cursor() as cur:
        await upsert_document(
            cur, doc_id, {BP_DATASET_ID_FIELD: set_id}, modified_at, set_id
        )


async def _delete_row(doc_id: str) -> None:
    """Delete a document from the database."""
    async with get_connection() as conn, conn.cursor() as cur:
        await cur.execute(f"DELETE FROM {DOCUMENT_TABLE} WHERE id = %s", (doc_id,))


async def _stored_and_indexed(
    sync_service: SyncService, doc_id: str
) -> tuple[bool, bool]:
    """Return whether the document is in the database and in the index."""
    async with get_connection() as conn, conn.cursor() as cur:
        stored = await get_document(cur, doc_id) is not None
    indexed = await sync_service.search.exists(
        index=sync_service._opensearch_index, id=doc_id
    )
    return stored, bool(indexed)


async def _has_logs(doc_id: str) -> bool:
    async with get_connection() as conn, conn.cursor() as cur:
        return bool(await read_document_logs(cur, doc_id))


async def _is_pending_sync(doc_id: str) -> bool:
    """Return True if the document is stored and waits for the next sync."""
    async with get_connection() as conn, conn.cursor() as cur:
        await cur.execute(
            f"SELECT synced_at IS NULL FROM {DOCUMENT_TABLE} WHERE id = %s", (doc_id,)
        )
        row = await cur.fetchone()
    return bool(row and row[0])


# Delete document
#


@pytest.mark.asyncio
async def test_delete_document(sync_service, doc_ids):
    doc_id, other_id, _ = doc_ids
    await _store_and_sync_bp(sync_service, doc_id, "set_1")
    await _store_and_sync_bp(sync_service, other_id, "set_1")

    assert await _stored_and_indexed(sync_service, doc_id) == (True, True)
    assert await _has_logs(doc_id)
    assert await _stored_and_indexed(sync_service, other_id) == (True, True)
    assert await _has_logs(other_id)

    async with get_connection() as conn, conn.cursor() as cur:
        await sync_service.delete_document(cur, doc_id)

    assert await _stored_and_indexed(sync_service, doc_id) == (False, False)
    assert not await _has_logs(doc_id)
    assert await _stored_and_indexed(sync_service, other_id) == (True, True)
    assert await _has_logs(other_id)


@pytest.mark.asyncio
async def test_delete_document_set(sync_service, doc_ids):
    id_1, id_2, id_3 = doc_ids
    set_id, other_set_id = _doc_id(), _doc_id()
    await _store_and_sync_bp(sync_service, id_1, set_id)
    await _store_and_sync_bp(sync_service, id_2, set_id)
    await _store_and_sync_bp(sync_service, id_3, other_set_id)

    async with get_connection() as conn, conn.cursor() as cur:
        deleted = await sync_service.delete_document_set(
            cur, BP_DATASET_ID_FIELD, set_id
        )

    assert sorted(deleted) == sorted([id_1, id_2])
    for doc_id in (id_1, id_2):
        assert await _stored_and_indexed(sync_service, doc_id) == (False, False)
        assert not await _has_logs(doc_id)
    assert await _stored_and_indexed(sync_service, id_3) == (True, True)
    assert await _has_logs(id_3)


@pytest.mark.asyncio
async def test_delete_document_rollbacks_if_the_index_delete_fails(
    sync_service, doc_ids, monkeypatch
):
    doc_id, _, _ = doc_ids
    set_id = _doc_id()
    await _store_and_sync_bp(sync_service, doc_id, set_id)
    monkeypatch.setattr(
        _INDEX_DELETE_BY_FIELD,
        AsyncMock(side_effect=SystemException("index_delete_fails")),
    )

    with pytest.raises(SystemException, match="index_delete_fails"):
        async with get_connection() as conn, conn.cursor() as cur:
            await sync_service.delete_document_set(cur, BP_DATASET_ID_FIELD, set_id)

    assert await _stored_and_indexed(sync_service, doc_id) == (True, True)
    assert await _has_logs(doc_id)


# Test sync_fields
#


@pytest.mark.asyncio
async def test_sync_fields_prunes_prunes_dropped_documents(sync_service, doc_ids):
    kept_doc_id, dropped_doc_id, other_doc_id = doc_ids
    set_id, other_set_id = _doc_id(), _doc_id()
    await _store_and_sync_bp(sync_service, kept_doc_id, set_id)
    await _store_and_sync_bp(sync_service, dropped_doc_id, set_id)
    await _store_and_sync_bp(sync_service, other_doc_id, other_set_id)
    await _delete_row(dropped_doc_id)
    await _delete_row(other_doc_id)
    await _store_bp(kept_doc_id, set_id, _CURRENT_MODIFIED_AT)

    async with get_connection() as conn, conn.cursor() as cur:
        await sync_service.sync_fields(cur)

    assert await _stored_and_indexed(sync_service, kept_doc_id) == (True, True)
    assert await _stored_and_indexed(sync_service, dropped_doc_id) == (False, False)
    assert await _stored_and_indexed(sync_service, other_doc_id) == (False, True)


@pytest.mark.asyncio
async def test_sync_fields_retries_pruning_after_a_failure(
    sync_service, doc_ids, monkeypatch
):
    kept_doc_id, dropped_doc_id, _ = doc_ids
    set_id = _doc_id()
    await _store_and_sync_bp(sync_service, kept_doc_id, set_id)
    await _store_and_sync_bp(sync_service, dropped_doc_id, set_id)
    await _delete_row(dropped_doc_id)
    await _store_bp(kept_doc_id, set_id, _CURRENT_MODIFIED_AT)

    with monkeypatch.context() as patched:
        patched.setattr(
            _INDEX_DELETE_BY_FIELD,
            AsyncMock(side_effect=SystemException("retries_pruning_after_a_failure")),
        )
        with pytest.raises(SystemException, match="retries_pruning_after_a_failure"):
            async with get_connection() as conn, conn.cursor() as cur:
                await sync_service.sync_fields(cur, kept_doc_id)

    assert await _is_pending_sync(kept_doc_id)
    assert await _stored_and_indexed(sync_service, dropped_doc_id) == (False, True)

    async with get_connection() as conn, conn.cursor() as cur:
        await sync_service.sync_fields(cur, kept_doc_id)

    assert not await _is_pending_sync(kept_doc_id)
    assert await _stored_and_indexed(sync_service, dropped_doc_id) == (False, False)


@pytest.mark.asyncio
async def test_sync_fields_refuses_document_without_set_id(sync_service, doc_ids):
    doc_id, _, _ = doc_ids
    async with get_connection() as conn, conn.cursor() as cur:
        await upsert_document(cur, doc_id, {}, _CURRENT_MODIFIED_AT, None)

    message = (
        f"Document {doc_id} has no document set id in field '{BP_DATASET_ID_FIELD}'."
    )
    with pytest.raises(SystemException, match=f"^{re.escape(message)}$"):
        async with get_connection() as conn, conn.cursor() as cur:
            await sync_service.sync_fields(cur, doc_id)

    assert await _is_pending_sync(doc_id)
    assert await _stored_and_indexed(sync_service, doc_id) == (True, False)
