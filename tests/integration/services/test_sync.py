"""Integration tests for SyncService."""

import os
import uuid

import pytest

from search_api.database.document import count_documents, upsert_document
from search_api.database.repository import get_connection
from search_api.services.sync import SyncService

os.environ.setdefault("POSTGRES_DB", os.environ["BP_POSTGRES_DB"])
os.environ.setdefault("POSTGRES_PORT", os.environ["BP_POSTGRES_PORT"])


def _doc_id() -> str:
    return f"test-{uuid.uuid4()}"


@pytest.mark.asyncio
async def test_delete_all_documents(bp_opensearch_index, bp_opensearch_index_name):
    sync_service = SyncService(bp_opensearch_index_name)
    try:
        async with get_connection() as conn:
            async with conn.cursor() as cur:
                database_count_before = await count_documents(cur)
                opensearch_count_before = (
                    await sync_service.search.count(index=bp_opensearch_index_name)
                )["count"]

                # Add two documents to the database.
                doc_id_1 = _doc_id()
                doc_id_2 = _doc_id()
                await upsert_document(cur, doc_id_1, {"image_id": "sync-test-1"}, None)
                await upsert_document(cur, doc_id_2, {"image_id": "sync-test-2"}, None)
                # Sync the documents to OpenSearch index.
                await sync_service.sync_fields(cur, doc_id_1)
                await sync_service.sync_fields(cur, doc_id_2)
                await sync_service.search.indices.refresh(
                    index=bp_opensearch_index_name
                )

                assert await count_documents(cur) == database_count_before + 2
                opensearch_count_after_sync = (
                    await sync_service.search.count(index=bp_opensearch_index_name)
                )["count"]
                assert opensearch_count_after_sync == opensearch_count_before + 2

                await sync_service.delete_all_documents(cur)

                assert await count_documents(cur) == 0
                opensearch_count_after_delete = (
                    await sync_service.search.count(index=bp_opensearch_index_name)
                )["count"]
                assert opensearch_count_after_delete == 0
    finally:
        await sync_service.search.close()
