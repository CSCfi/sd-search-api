"""Generic OpenSearch query builders and indexing helpers."""

import logging
from datetime import timedelta
from typing import Any

import isodate  # type: ignore[import-untyped]

from aiocache import cached  # type: ignore[import-untyped]
from opensearchpy import AsyncOpenSearch, helpers

from search_api.conf import opensearch_config as _opensearch_config
from search_api.exceptions import SystemException

logging.basicConfig(level=logging.INFO)

_FETCH_INDEXED_KEYWORDS_TTL = 60 * 60 * 4  # 4 hours


def create_search() -> AsyncOpenSearch:
    """Create an OpenSearch client from the application configuration."""
    cfg = _opensearch_config()
    return AsyncOpenSearch(
        hosts=[{"host": cfg.OPENSEARCH_HOST, "port": cfg.OPENSEARCH_PORT}],
        http_auth=(cfg.OPENSEARCH_USER, cfg.OPENSEARCH_PASSWORD),
        use_ssl=True,
        verify_certs=False,
    )


async def create_index(
    search: AsyncOpenSearch, index: str, body: dict[str, Any]
) -> None:
    """Create an OpenSearch index with the given settings and mappings.

    Raises SystemException if the index already exists. OpenSearch cannot change
    an existing field's type, so an index created with the wrong (e.g. dynamic)
    mapping must be deleted and recreated deliberately rather than silently left
    in place.

    :param search: The OpenSearch client.
    :param index: The OpenSearch index name.
    :param body: The index body (settings and mappings) to create it with.
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

    success, failed = await helpers.async_bulk(
        search, actions, refresh=False, chunk_size=1000, raise_on_error=False
    )

    if failed:
        raise SystemException(f"{failed} document(s) failed to index")


# Names the reverse_nested aggregation that counts documents rather than group items.
_DOCUMENTS_AGG_NAME = "documents"

_MAX_KEYWORD_VALUES = 10000  # Upper bound on distinct values for one field.


@cached(ttl=_FETCH_INDEXED_KEYWORDS_TTL)
async def fetch_indexed_keywords(
    search: AsyncOpenSearch,
    index_name: str,
    field_name: str,
    *,
    document_filter: dict[str, Any] | None = None,
    group_item_filter: dict[str, Any] | None = None,
) -> dict[str, int]:
    """Return each distinct value of a keyword field with its count.

    Example for ``field_name="diagnosis.diagnosis"`` over document::
        {
         "scope": "clinical",
         "diagnosis": [{"diagnosis": "73211009",
                        "qualifiers": ["observation:confirmed"]},
                       {"diagnosis": "38341003",
                        "qualifiers": ["observation:candidate"]}]
        }

    called with
    ``document_filter={"term": {"scope": "clinical"}}``
    and
    ``group_item_filter={"terms": {"diagnosis.qualifiers": ["observation:confirmed"]}}``,

    The request body built is::

        {"size": 0,                                    # aggregations only, no hits
         "query": {"term": {"scope": "clinical"}},     # document_filter: which documents
         "aggs": {"group_items": {                     # count over the diagnosis items
           "nested": {"path": "diagnosis"},
           "aggs": {"qualified_items": {               # group_item_filter: which items
             "filter": {"terms": {"diagnosis.qualifiers": ["observation:confirmed"]}},
             "aggs": {"field_values": {                # one bucket per diagnosis value
               "terms": {"field": "diagnosis.diagnosis"},
               "aggs": {"documents": {                 # count documents, not items
                 "reverse_nested": {}}}}}}}}}}

    The response nests using the aggregation names::

        {"aggregations": {
          "group_items": {
           "qualified_items": {
            "field_values": {
             "buckets": [{"key": "73211009",
                          "doc_count": 2,               # matching items
                          "documents": {"doc_count": 1}}]}}}}}

        ->  {"73211009": 1}

    A bucket's own ``doc_count`` counts group items, so one document holding two
    matching items would count twice for the same value. The reverse_nested
    aggregation climbs back to the documents, and its count is the one returned.

    A field with no group has no wrappers: ``field_values`` sits directly under
    ``aggs``, its buckets already count documents, and no reverse_nested is added.

    Args:
        search: OpenSearch client.
        index_name: OpenSearch index to query.
        field_name: Full dotted field path.
        document_filter: Restricts which documents are included.
        group_item_filter: Restricts which of a group's items within those
            documents are included. Nested fields only.

    Returns:
        Mapping of keyword value to the number of documents carrying it.
    """
    nested_path, separator, _ = field_name.partition(".")
    is_nested = bool(separator)

    if group_item_filter is not None and not is_nested:
        raise SystemException(
            f"Cannot filter the group items of '{field_name}' because the field is not in a group."
        )

    # Aggregations are built from the inside out. A bucket's own doc_count counts
    # group items, so for a field in a group reverse_nested climbs back to the
    # documents holding those items and counts documents instead.
    field_values: dict[str, Any] = {
        "terms": {"field": field_name, "size": _MAX_KEYWORD_VALUES}
    }
    if is_nested:
        field_values["aggs"] = {_DOCUMENTS_AGG_NAME: {"reverse_nested": {}}}
    aggregations: dict[str, Any] = {"field_values": field_values}
    if group_item_filter is not None:
        aggregations = {
            "qualified_items": {"filter": group_item_filter, "aggs": aggregations}
        }
    if is_nested:
        aggregations = {
            "group_items": {"nested": {"path": nested_path}, "aggs": aggregations}
        }

    # "size": 0 discards the matching hits, leaving only the aggregations.
    body: dict[str, Any] = {"size": 0, "aggs": aggregations}
    if document_filter is not None:
        body["query"] = document_filter

    response = await search.search(index=index_name, body=body)

    # The response nests like the request.
    result = response["aggregations"]
    if is_nested:
        result = result["group_items"]
    if group_item_filter is not None:
        result = result["qualified_items"]

    return {
        bucket["key"]: (
            bucket[_DOCUMENTS_AGG_NAME]["doc_count"]
            if is_nested
            else bucket["doc_count"]
        )
        for bucket in result["field_values"]["buckets"]
    }


def build_match_query(field_id: str, value: str) -> dict[str, Any]:
    """Build an OpenSearch match query."""
    return {"match": {field_id: value}}


def build_term_query(field_id: str, value: str) -> dict[str, Any]:
    """Build an OpenSearch term query."""
    return {"term": {field_id: value}}


def build_terms_query(field_id: str, values: list[str]) -> dict[str, Any]:
    """Build an OpenSearch terms query matching any of the given values."""
    return {"terms": {field_id: values}}


def iso8601_duration_to_days(duration: str) -> int:
    """Convert an ISO-8601 duration string to number of days.

    Uses: 1 year = 365 days, 1 month = 30 days.
    """
    d = isodate.parse_duration(duration)
    if isinstance(d, timedelta):
        return d.days
    return int(d.years) * 365 + int(d.months) * 30 + d.tdelta.days


def build_iso8601_range_query(field_id: str, value: str) -> dict[str, Any]:
    """Build an OpenSearch range query from an ISO-8601 duration range."""
    parts = value.split("-", 1)
    gte = iso8601_duration_to_days(parts[0])
    lte = iso8601_duration_to_days(parts[1]) if len(parts) > 1 else gte
    return {"range": {field_id: {"gte": gte, "lte": lte}}}


def or_queries(queries: list[dict[str, Any]]) -> dict[str, Any]:
    return {"bool": {"should": queries, "minimum_should_match": 1}}
