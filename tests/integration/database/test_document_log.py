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
    """Two document ids, whose rows are deleted afterwards."""
    ids = [f"test-{uuid.uuid4()}", f"test-{uuid.uuid4()}"]
    yield ids
    async with get_cursor() as cur:
        await cur.execute(
            f"DELETE FROM {DOCUMENT_LOG_TABLE} WHERE document_id = ANY(%s)", (ids,)
        )


@pytest.mark.asyncio
async def test_delete_document_logs(document_ids):
    doc_id, other_id = document_ids
    async with get_cursor() as cur:
        await write_document_log(cur, _log(doc_id, "First."))
        await write_document_log(cur, _log(doc_id, "Second."))
        await write_document_log(cur, _log(other_id, "Another document's."))

        deleted = await delete_document_logs(cur, doc_id)

        assert deleted == 2
        assert await read_document_logs(cur, doc_id) == []
        assert [log.message for log in await read_document_logs(cur, other_id)] == [
            "Another document's."
        ]
