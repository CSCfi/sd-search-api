from typing import Any

from opensearchpy import AsyncOpenSearch, helpers

from search_api.exceptions import SystemException


async def create_index(
    search: AsyncOpenSearch, index: str, body: dict[str, Any]
) -> None:
    """Create an OpenSearch index with the given settings and mappings.

    :param search: The OpenSearch client.
    :param index: The OpenSearch index name.
    :param body: The index body (settings and mappings) to create it with.
    :raises SystemException: If the index already exists.
    """
    if await search.indices.exists(index=index):
        raise SystemException(
            f"Index '{index}' already exists. Delete it explicitly first if you "
            "intend to recreate it (e.g. `curl -X DELETE .../<index>`), then rerun "
            "this command and resync."
        )
    await search.indices.create(index=index, body=body)


async def delete_all_documents(search: AsyncOpenSearch, index: str) -> int:
    """Delete all documents from the OpenSearch index and return the number of deleted documents.

    :param search: The OpenSearch client.
    :param index: The OpenSearch index name.
    :return: The number of documents deleted.
    """
    if not await search.indices.exists(index=index):
        return 0
    response = await search.delete_by_query(
        index=index, body={"query": {"match_all": {}}}, refresh=True
    )
    return response.get("deleted", 0)


async def index_document(
    search: AsyncOpenSearch,
    index: str,
    id: str,
    doc: dict[str, Any],
) -> None:
    """Index a document in OpenSearch.

    :param search: The OpenSearch client.
    :param index: The OpenSearch index name.
    :param id: Document id.
    :param doc: The OpenSearch document to index.
    """
    await search.index(
        index=index,
        id=id,
        body=doc,
        refresh=False,
    )


async def index_documents(
    search: AsyncOpenSearch,
    index: str,
    ids: list[str],
    docs: list[dict[str, Any]],
) -> None:
    """Bulk index documents in OpenSearch.

    :param search: The OpenSearch client.
    :param index: The OpenSearch index name.
    :param ids: Document ids.
    :param docs: The OpenSearch documents to index.
    :raises SystemException: If ids and docs differ in length, or if any
        document fails to index.
    """
    if len(ids) != len(docs):
        raise SystemException("Different number of ids and docs")

    actions = (
        {
            "_index": index,
            "_id": _id,
            "_source": doc,
        }
        for _id, doc in zip(ids, docs)
    )

    _, failed = await helpers.async_bulk(
        search, actions, refresh=False, chunk_size=1000, raise_on_error=False
    )

    if failed:
        raise SystemException(f"{failed} document(s) failed to index")
