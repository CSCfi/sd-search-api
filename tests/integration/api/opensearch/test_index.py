"""Tests for deleting documents from an OpenSearch index."""

import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from opensearchpy import AsyncOpenSearch

from search_api.api.opensearch.client import create_search
from search_api.api.opensearch.index import (
    delete_all_documents,
    delete_documents_by_field,
    delete_documents_by_ids,
)

_SET_FIELD = "set_id"


@pytest_asyncio.fixture
async def search() -> AsyncIterator[AsyncOpenSearch]:
    client = create_search()
    yield client
    await client.close()


@pytest_asyncio.fixture
async def index(search: AsyncOpenSearch) -> AsyncIterator[str]:
    """A new, empty index for one test, deleted afterwards.

    With set id keyword field. It is not refreshed automatically, so a search
    finds a document only after a refresh.
    """
    name = f"test-index-{uuid.uuid4().hex}"
    await search.indices.create(
        index=name,
        body={
            "settings": {"refresh_interval": "-1"},
            "mappings": {"properties": {_SET_FIELD: {"type": "keyword"}}},
        },
    )
    yield name
    await search.indices.delete(index=name, ignore=[404])


async def _add(
    search: AsyncOpenSearch, index: str, doc_id: str, set_id: str, refresh: bool
) -> None:
    """Add a document of a document set to the index.

    With refresh, a search finds it at once. Without, only after the next refresh.
    """
    await search.index(
        index=index, id=doc_id, body={_SET_FIELD: set_id}, refresh=refresh
    )


async def _ids(search: AsyncOpenSearch, index: str, refresh: bool) -> set[str]:
    """Return the ids of the documents a search finds in the index.

    With refresh, the index is refreshed first, so every document is found. Without,
    only documents already refreshed are found.
    """
    if refresh:
        await search.indices.refresh(index=index)
    response = await search.search(index=index, body={"query": {"match_all": {}}})
    return {hit["_id"] for hit in response["hits"]["hits"]}


# delete_documents_by_ids
#


@pytest.mark.asyncio
async def test_delete_documents_by_ids(search, index):
    for doc_id in ("id_1", "id_2", "id_3"):
        await _add(search, index, doc_id, "set_1", refresh=True)

    assert await delete_documents_by_ids(search, index, ["id_1", "id_2"]) == 2

    assert await _ids(search, index, refresh=True) == {"id_3"}


@pytest.mark.asyncio
async def test_delete_documents_by_ids_ignores_missing_ids(search, index):
    """A document not in the index is not counted, and is no failure."""
    await _add(search, index, "id_1", "set_1", refresh=True)

    assert await delete_documents_by_ids(search, index, ["id_1", "missing"]) == 1

    assert await _ids(search, index, refresh=True) == set()


@pytest.mark.asyncio
async def test_delete_documents_by_ids_refreshes_index(search, index):
    await _add(search, index, "id_1", "set_1", refresh=True)

    await delete_documents_by_ids(search, index, ["id_1"])

    assert await _ids(search, index, refresh=False) == set()


@pytest.mark.asyncio
async def test_delete_documents_by_ids_deletes_unrefreshed_documents(search, index):
    """Deleting by id needs no search, so it finds a document not yet refreshed."""
    await _add(search, index, "id_1", "set_1", refresh=False)

    assert await delete_documents_by_ids(search, index, ["id_1"]) == 1

    assert await _ids(search, index, refresh=True) == set()


@pytest.mark.asyncio
async def test_delete_documents_by_ids_without_ids(search, index):
    await _add(search, index, "id_1", "set_1", refresh=True)

    assert await delete_documents_by_ids(search, index, []) == 0

    assert await _ids(search, index, refresh=True) == {"id_1"}


@pytest.mark.asyncio
async def test_delete_documents_by_ids_no_index(search):
    assert await delete_documents_by_ids(search, "no-index", ["id_1"]) == 0


# delete_documents_by_field
#


@pytest.mark.asyncio
async def test_delete_documents_by_field(search, index):
    await _add(search, index, "id_1", "set_1", refresh=True)
    await _add(search, index, "id_2", "set_1", refresh=True)
    await _add(search, index, "id_3", "set_2", refresh=True)

    assert (
        await delete_documents_by_field(
            search,
            index,
            _SET_FIELD,
            ["set_1"],
            [],
        )
        == 2
    )

    assert await _ids(search, index, refresh=True) == {"id_3"}


@pytest.mark.asyncio
async def test_delete_documents_by_field_deletes_unrefreshed_documents(search, index):
    await _add(search, index, "id_1", "set_1", refresh=False)

    assert (
        await delete_documents_by_field(
            search,
            index,
            _SET_FIELD,
            ["set_1"],
            [],
        )
        == 1
    )

    assert await _ids(search, index, refresh=True) == set()


@pytest.mark.asyncio
async def test_delete_documents_by_field_deletes_unrefreshed_changes(search, index):
    await _add(search, index, "id_1", "set_1", refresh=True)
    await _add(search, index, "id_1", "set_1", refresh=False)

    assert (
        await delete_documents_by_field(
            search,
            index,
            _SET_FIELD,
            ["set_1"],
            [],
        )
        == 1
    )

    assert await _ids(search, index, refresh=True) == set()


@pytest.mark.asyncio
async def test_delete_documents_by_field_refreshes_index(search, index):
    await _add(search, index, "id_1", "set_1", refresh=True)

    await delete_documents_by_field(search, index, _SET_FIELD, ["set_1"], [])

    assert await _ids(search, index, refresh=False) == set()


@pytest.mark.asyncio
async def test_delete_documents_by_field_without_index(search):
    assert (
        await delete_documents_by_field(
            search,
            "no-such-index",
            _SET_FIELD,
            ["set_1"],
            [],
        )
        == 0
    )


# delete_documents_by_field, keeping some
#


@pytest.mark.asyncio
async def test_delete_documents_by_field_keeps_those_ids_and_other_values(
    search, index
):
    await _add(search, index, "id_1", "set_1", refresh=True)
    await _add(search, index, "id_2", "set_1", refresh=False)
    await _add(search, index, "id_3", "set_1", refresh=True)
    await _add(search, index, "id_4", "set_2", refresh=True)

    deleted = await delete_documents_by_field(
        search,
        index,
        _SET_FIELD,
        ["set_1"],
        ["id_1"],
    )

    assert deleted == 2
    assert await _ids(search, index, refresh=True) == {"id_1", "id_4"}


@pytest.mark.asyncio
async def test_delete_documents_by_field_keeps_several_ids(search, index):
    for doc_id in ("id_1", "id_2", "id_3"):
        await _add(search, index, doc_id, "set_1", refresh=True)

    deleted = await delete_documents_by_field(
        search,
        index,
        _SET_FIELD,
        ["set_1"],
        ["id_1", "id_3"],
    )

    assert deleted == 1
    assert await _ids(search, index, refresh=True) == {"id_1", "id_3"}


@pytest.mark.asyncio
async def test_delete_documents_by_field_keeping_every_id_deletes_nothing(
    search, index
):
    await _add(search, index, "id_1", "set_1", refresh=True)
    await _add(search, index, "id_2", "set_1", refresh=True)

    deleted = await delete_documents_by_field(
        search,
        index,
        _SET_FIELD,
        ["set_1"],
        ["id_1", "id_2"],
    )

    assert deleted == 0
    assert await _ids(search, index, refresh=True) == {"id_1", "id_2"}


@pytest.mark.asyncio
async def test_delete_documents_by_field_ignores_kept_ids_not_in_the_index(
    search, index
):
    """A kept id may be stored but not yet synced, so absent from the index."""
    await _add(search, index, "id_1", "set_1", refresh=True)
    await _add(search, index, "id_2", "set_1", refresh=True)

    deleted = await delete_documents_by_field(
        search,
        index,
        _SET_FIELD,
        ["set_1"],
        ["id_1", "not-synced"],
    )

    assert deleted == 1
    assert await _ids(search, index, refresh=True) == {"id_1"}


@pytest.mark.asyncio
async def test_delete_documents_by_field_keeps_each_values_ids(search, index):
    await _add(search, index, "id_1", "set_1", refresh=True)
    await _add(search, index, "id_2", "set_1", refresh=True)
    await _add(search, index, "id_3", "set_2", refresh=False)
    await _add(search, index, "id_4", "set_2", refresh=True)
    await _add(search, index, "id_5", "set_3", refresh=True)

    deleted = await delete_documents_by_field(
        search,
        index,
        _SET_FIELD,
        ["set_1", "set_2"],
        ["id_1"],
    )

    assert deleted == 3
    assert await _ids(search, index, refresh=True) == {"id_1", "id_5"}


@pytest.mark.asyncio
async def test_delete_documents_by_field_with_many_values(search, index):
    await _add(search, index, "id_1", "set_1", refresh=True)
    await _add(search, index, "id_2", "set_1", refresh=True)

    values = [f"set_{n}" for n in range(1, 2001)]
    deleted = await delete_documents_by_field(
        search, index, _SET_FIELD, values, ["id_1"]
    )

    assert deleted == 1
    assert await _ids(search, index, refresh=True) == {"id_1"}


# delete_all_documents
#


@pytest.mark.asyncio
async def test_delete_all_deletes_every_document(search, index):
    await _add(search, index, "id_1", "set_1", refresh=True)
    await _add(search, index, "id_2", "set_2", refresh=False)

    assert await delete_all_documents(search, index) == 2

    assert await _ids(search, index, refresh=True) == set()


@pytest.mark.asyncio
async def test_delete_all_no_index(search):
    assert await delete_all_documents(search, "no-index") == 0
