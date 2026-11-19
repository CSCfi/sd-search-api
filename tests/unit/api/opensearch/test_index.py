from unittest.mock import AsyncMock, MagicMock

import pytest

from search_api.api.opensearch import index as index_module
from search_api.api.opensearch.index import _field_types, delete_documents_by_ids
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


def test_field_types():
    """Every field is listed by dotted path, a nested group and each of its fields."""
    properties = {
        "scope": {"type": "keyword"},
        "specimen": {
            "type": "nested",
            "properties": {
                "sex": {"type": "keyword"},
                "age_at_extraction": {"type": "integer_range"},
            },
        },
        "empty": {"type": "nested", "properties": {}},
        "object": {"properties": {"name": {"type": "text"}}},
    }

    assert _field_types(properties) == {
        "scope": "keyword",
        "specimen": "nested",
        "specimen.sex": "keyword",
        "specimen.age_at_extraction": "integer_range",
        "empty": "nested",
        "object": "object",
        "object.name": "text",
    }


def test_field_types_empty():
    assert _field_types({}) == {}
