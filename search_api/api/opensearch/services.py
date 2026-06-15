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


@cached(ttl=_FETCH_INDEXED_KEYWORDS_TTL)
async def fetch_indexed_keywords(
    search: AsyncOpenSearch, index_name: str, field_name: str
) -> dict[str, int]:
    """Return all keyword values and their document counts for a keyword field.

    For nested fields (e.g. ``"blocks.species"``) a nested aggregation is used
    automatically.

    Args:
        search: OpenSearch client.
        index_name: OpenSearch index to query.
        field_name: Full dotted field path.

    Returns:
        Mapping of keyword value to document count.
    """
    parts = field_name.split(".", 1)
    if len(parts) == 2:
        nested_path = parts[0]
        body: dict[str, Any] = {
            "size": 0,
            "aggs": {
                "nested_values": {
                    "nested": {"path": nested_path},
                    "aggs": {
                        "values": {"terms": {"field": field_name, "size": 10000}},
                    },
                }
            },
        }
        resp = await search.search(index=index_name, body=body)
        buckets = resp["aggregations"]["nested_values"]["values"]["buckets"]
    else:
        body = {
            "size": 0,
            "aggs": {
                "values": {"terms": {"field": field_name, "size": 10000}},
            },
        }
        resp = await search.search(index=index_name, body=body)
        buckets = resp["aggregations"]["values"]["buckets"]

    return {bucket["key"]: bucket["doc_count"] for bucket in buckets}


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
