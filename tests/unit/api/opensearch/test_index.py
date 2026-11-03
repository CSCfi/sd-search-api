from unittest.mock import AsyncMock, MagicMock

import pytest

from search_api.api.opensearch import index as index_module
from search_api.api.opensearch.index import delete_documents_by_ids
from search_api.exceptions import SystemException


def _search() -> MagicMock:
    search = MagicMock()
    search.indices.exists = AsyncMock(return_value=True)
    return search


@pytest.mark.asyncio
async def test_delete_documents_by_ids_failure(monkeypatch):
    """A failed delete raises an error, but a missing document does not.

    The bulk call to OpenSearch is replaced by a fake one. It answers that one
    document was deleted, one failed with 429 (too many requests), and one was not
    found (404). The error must count only the 429 failure, a missing document is
    not a failure.
    """
    bulk = AsyncMock(
        return_value=(
            1,
            [{"delete": {"status": 429}}, {"delete": {"status": 404}}],
        )
    )
    monkeypatch.setattr(index_module.helpers, "async_bulk", bulk)

    with pytest.raises(SystemException, match=r"^1 document\(s\) failed to delete$"):
        await delete_documents_by_ids(_search(), "index", ["id1", "id2", "id3"])


@pytest.mark.asyncio
async def test_delete_documents_by_ids_success(monkeypatch):
    """A missing document is not a failure."""

    bulk = AsyncMock(return_value=(2, [{"delete": {"status": 404}}]))
    monkeypatch.setattr(index_module.helpers, "async_bulk", bulk)

    assert await delete_documents_by_ids(_search(), "index", ["id1", "id2", "id3"]) == 2
