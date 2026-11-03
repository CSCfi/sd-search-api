import os
import uuid

import pytest
import pytest_asyncio

from search_api.database.document_log import (
    DOCUMENT_LOG_TABLE,
    delete_document_logs,
    read_document_logs,
    write_document_log,
)
from search_api.database.models import StoredDocumentLog
from search_api.database.repository import get_cursor

os.environ.setdefault("POSTGRES_DB", os.environ["BP_POSTGRES_DB"])
os.environ.setdefault("POSTGRES_PORT", os.environ["BP_POSTGRES_PORT"])


def _log(document_id: str, message: str) -> StoredDocumentLog:
    return StoredDocumentLog(
        document_id=document_id,
        field_id="animal_species",
        severity="ERROR",
        message=message,
    )


@pytest_asyncio.fixture
async def document_ids():
    """Three document ids, whose rows are deleted afterwards."""
    ids = [f"test-{uuid.uuid4()}" for _ in range(3)]
    yield ids
    async with get_cursor() as cur:
        await cur.execute(
            f"DELETE FROM {DOCUMENT_LOG_TABLE} WHERE document_id = ANY(%s)", (ids,)
        )


@pytest.mark.asyncio
async def test_delete_document_logs(document_ids):
    """Every log row of the given documents is deleted, and no other document's."""
    two_log_rows_id, one_log_row_id, kept_log_row_id = document_ids
    async with get_cursor() as cur:
        await write_document_log(cur, _log(two_log_rows_id, "test1"))
        await write_document_log(cur, _log(two_log_rows_id, "test2"))
        await write_document_log(cur, _log(one_log_row_id, "test3"))
        await write_document_log(cur, _log(kept_log_row_id, "test4"))

        assert await delete_document_logs(cur, [two_log_rows_id, one_log_row_id]) == 3
        assert await delete_document_logs(cur, []) == 0

        assert await read_document_logs(cur, two_log_rows_id) == []
        assert await read_document_logs(cur, one_log_row_id) == []
        assert [
            log.message for log in await read_document_logs(cur, kept_log_row_id)
        ] == ["test4"]
