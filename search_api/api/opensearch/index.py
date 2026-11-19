from collections.abc import Sequence
from typing import Any, cast

from opensearchpy import AsyncOpenSearch, helpers

from search_api.api.opensearch.clauses import build_terms_clause
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


def _field_types(properties: dict[str, Any], prefix: str = "") -> dict[str, str]:
    """Return the type of every field in the mapping properties, by dotted path.

    An object field has no type in a mapping, so it is given "object".
    """
    types: dict[str, str] = {}
    for name, mapping in properties.items():
        path = f"{prefix}{name}"
        types[path] = mapping.get("type", "object")
        types |= _field_types(mapping.get("properties", {}), f"{path}.")
    return types


async def update_index_mapping(
    search: AsyncOpenSearch, index: str, body: dict[str, Any]
) -> list[str]:
    """Add new fields in the generated mapping to an existing OpenSearch index.

    Only additive changes are supported. OpenSearch refuses a changed field
    type or analyzer, and does not support field removal. Both require the
    index to be recreated.

    :param search: The OpenSearch client.
    :param index: The OpenSearch index name.
    :param body: The new index body, containing mappings.
    :return: The paths of the added fields, sorted.
    :raises SystemException: If the index does not exist, or a field is removed or
        its type changed.
    :raises RequestError: If OpenSearch refuses the mapping, e.g. a changed
        analyzer.
    """
    if not await search.indices.exists(index=index):
        raise SystemException(f"Index '{index}' does not exist.")

    response = await search.indices.get_mapping(index=index)
    current = _field_types(response[index]["mappings"].get("properties", {}))
    mappings = body["mappings"]
    new = _field_types(mappings.get("properties", {}))

    removed = sorted(current.keys() - new.keys())
    if removed:
        raise SystemException(
            f"Fields can't be removed from an existing index: {', '.join(removed)}. "
            "A removed field needs the index recreated."
        )

    # Only the type is compared. Other parameters may change in place, and
    # OpenSearch refuses those that cannot with its own error.
    changed = sorted(path for path in current if current[path] != new[path])
    if changed:
        raise SystemException(
            f"Fields can't be changed in an existing index: {', '.join(changed)}. "
            "A changed field needs the index recreated."
        )

    await search.indices.put_mapping(index=index, body=mappings)
    return sorted(new.keys() - current.keys())


async def _delete_by_query(
    search: AsyncOpenSearch, index: str, query: dict[str, Any]
) -> int:
    """Delete the documents matching the query and return the number of deleted documents."""
    if not await search.indices.exists(index=index):
        return 0
    # Written documents become searchable only after a refresh. A sync does not
    # refresh, so a search may not yet see what it wrote.
    # This query finds documents by searching, then deletes each one only if it has
    # not changed since. Without a refresh, it would miss new documents, and stop
    # with an error on changed ones.
    await search.indices.refresh(index=index)
    response = await search.delete_by_query(
        index=index, body={"query": query}, refresh=True
    )
    return response.get("deleted", 0)


async def delete_all_documents(search: AsyncOpenSearch, index: str) -> int:
    """Delete all documents from the OpenSearch index and return the number of deleted documents.

    :param search: The OpenSearch client.
    :param index: The OpenSearch index name.
    :return: The number of documents deleted.
    """
    return await _delete_by_query(search, index, {"match_all": {}})


async def delete_documents_by_field(
    search: AsyncOpenSearch,
    index: str,
    field_id: str,
    values: Sequence[str],
    keep_doc_ids: Sequence[str],
) -> int:
    """Delete the documents whose keyword field has one of the values, except some.

    The query has two clauses however many values it has, as OpenSearch limits the
    number of clauses.

    :param values: The field values whose documents are deleted.
    :param keep_doc_ids: The ids of documents not to delete.
    :return: The number of documents deleted.
    """
    if not values:
        return 0
    query = {
        "bool": {
            "filter": [build_terms_clause(field_id, list(values))],
            "must_not": [{"ids": {"values": list(keep_doc_ids)}}],
        }
    }
    return await _delete_by_query(search, index, query)


async def delete_documents_by_ids(
    search: AsyncOpenSearch, index: str, ids: list[str]
) -> int:
    """Delete the documents with the given ids and return the number of deleted documents.

    Each document is deleted by its id, without a search. So no refresh is needed
    first, unlike a delete by query.

    :param search: The OpenSearch client.
    :param index: The OpenSearch index name.
    :param ids: The document ids.
    :return: The number of documents deleted.
    :raises SystemException: If any document fails to delete.
    """
    if not ids or not await search.indices.exists(index=index):
        return 0

    actions = ({"_op_type": "delete", "_index": index, "_id": _id} for _id in ids)
    # Refreshed, so a search no longer finds the deleted documents.
    deleted, errors = await helpers.async_bulk(
        search, actions, refresh=True, raise_on_error=False
    )

    # A document not in the index is not considered an error. It may never have been
    # synced, or it may have been deleted already.
    failed = [
        error
        for error in cast(list[dict[str, Any]], errors)
        if error["delete"].get("status") != 404  # not found
    ]
    if failed:
        raise SystemException(f"{len(failed)} document(s) failed to delete")
    return deleted


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
