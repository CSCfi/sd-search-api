"""Document search and count: plain (unaggregated) and grouped (composite aggregation)."""

from collections.abc import AsyncIterator, Callable
from typing import Any, TypeVar

from opensearchpy import AsyncOpenSearch, NotFoundError

R = TypeVar("R")


async def count_documents(
    search: AsyncOpenSearch, index: str, query_clause: dict[str, Any] | None = None
) -> int:
    """Return the number of documents in the index given the query clause.

    :param search: The OpenSearch client.
    :param index: The OpenSearch index name.
    :param query_clause: Restricts which documents are counted.
    :return: The number of matching documents. Zero if the index does not exist.
    """
    try:
        resp = await search.count(
            index=index, body={"query": query_clause} if query_clause else None
        )
    except NotFoundError:
        return 0
    return int(resp["count"])


def _paged_documents_request(
    query_clause: dict[str, Any],
    page_size: int,
    source_fields: list[str],
    sort_field: str,
    search_after: list[Any] | None,
) -> dict[str, Any]:
    """Build the OpenSearch request body for a paginated search.

    :param query_clause: Restricts which documents match.
    :param page_size: How many hits to request per page.
    :param source_fields: Restricts ``_source`` (indexed
    OpenSearch document) to these fields, the only ones a caller reads per hit.
    :param sort_field: The field pages are ordered by.
    :param search_after: The previous page's last hit's sort value, to
        advance, or None for the first page.
    :return: The OpenSearch request body.
    """
    body: dict[str, Any] = {
        "size": page_size,
        "query": query_clause,
        "_source": source_fields,
        "sort": [{sort_field: "asc"}],
    }
    if search_after:
        body["search_after"] = search_after
    return body


async def _iter_paged_documents(
    search: AsyncOpenSearch,
    index_name: str,
    page_size: int,
    build_body: Callable[[list[Any] | None], dict[str, Any]],
) -> AsyncIterator[dict[str, Any]]:
    """Yield every matching document, paging via search_after until exhausted.

    :param search: OpenSearch client.
    :param index_name: OpenSearch index to query.
    :param page_size: Hit count requested per page.
    :param build_body: Called with the previous page's sort value (``None``
        for the first page); must return a body built by ``_paged_documents_request``.
    :return: An async iterator over every matching document, across every page.
    """
    search_after: list[Any] | None = None
    while True:
        resp = await search.search(index=index_name, body=build_body(search_after))
        hits = resp["hits"]["hits"]
        for hit in hits:
            yield hit
        if len(hits) < page_size:
            break
        search_after = hits[-1]["sort"]


async def get_documents(
    search: AsyncOpenSearch,
    index_name: str,
    query_clause: dict[str, Any],
    page_size: int,
    source_fields: list[str],
    sort_field: str,
    build_record: Callable[[dict[str, Any]], R],
) -> list[R]:
    """Return one record per matching document, built from its ``_source`` (indexed OpenSearch document).

    :param search: OpenSearch client.
    :param index_name: OpenSearch index to query.
    :param query_clause: Restricts which documents match.
    :param page_size: How many hits to request per page.
    :param source_fields: Restricts what is fetched per hit.
    :param sort_field: The field pages are ordered by.
    :param build_record: Turns each hit's ``_source`` (indexed OpenSearch document) into a record.
    :return: One record per matching document.
    """
    return [
        build_record(hit["_source"])
        async for hit in _iter_paged_documents(
            search,
            index_name,
            page_size,
            lambda search_after: _paged_documents_request(
                query_clause, page_size, source_fields, sort_field, search_after
            ),
        )
    ]


# Names the composite aggregation paged by _iter_paged_buckets.
_COMPOSITE_AGG_NAME = "pages"


def top_hits_sub_agg(source_fields: list[str], size: int = 1) -> dict[str, Any]:
    """Build a top_hits sub-aggregation, fetching fields from ``size``
    representative documents within a bucket. A composite aggregation's
    ``sub_aggs`` value when a bucket's own key does not carry every field a
    result needs.

    :param source_fields: Fields to fetch from each representative document.
    :param size: How many representative documents to fetch per bucket.
    :return: The top_hits sub-aggregation clause.
    """
    return {"top_hits": {"size": size, "_source": source_fields}}


def top_hits_source(bucket: dict[str, Any], name: str) -> dict[str, Any]:
    """Return the first representative document's ``_source`` from a
    bucket's named top_hits sub-aggregation (built by ``top_hits_sub_agg``),
    or ``{}`` if that bucket has no hits.

    :param bucket: One bucket of a composite aggregation response.
    :param name: The top_hits sub-aggregation's name within that bucket.
    :return: The representative document's ``_source``, or ``{}``.
    """
    hits = bucket[name]["hits"]["hits"]
    return hits[0]["_source"] if hits else {}


def _paged_buckets_request(
    query_clause: dict[str, Any],
    sources: list[dict[str, Any]],
    page_size: int,
    after_key: dict[str, Any] | None,
    sub_aggs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the request body for one page of a composite aggregation.

    :param query_clause: Restricts which documents are aggregated.
    :param sources: The composite's bucket keys, in order — e.g. one field to
        group by, or several to bucket by their combination.
    :param page_size: How many buckets to request per page.
    :param after_key: The previous page's after_key, to advance to the next
        page, or None for the first page.
    :param sub_aggs: Extra sub-aggregation(s) to run for every bucket.
    :return: The OpenSearch request body.
    """
    composite: dict[str, Any] = {"size": page_size, "sources": sources}
    if after_key:
        composite["after"] = after_key

    aggs: dict[str, Any] = {"composite": composite}
    if sub_aggs:
        aggs["aggs"] = sub_aggs

    return {"size": 0, "query": query_clause, "aggs": {_COMPOSITE_AGG_NAME: aggs}}


async def _iter_paged_buckets(
    search: AsyncOpenSearch,
    index_name: str,
    page_size: int,
    build_body: Callable[[dict[str, Any] | None], dict[str, Any]],
) -> AsyncIterator[dict[str, Any]]:
    """Yield every bucket of a composite aggregation, paging until exhausted.

    :param search: OpenSearch client.
    :param index_name: OpenSearch index to query.
    :param page_size: Bucket count requested per page.
    :param build_body: Called with the previous page's after_key (``None`` for
        the first page); must return a body built by ``_paged_buckets_request``.
    :return: An async iterator over every bucket, across every page.
    """
    after_key: dict[str, Any] | None = None
    while True:
        resp = await search.search(index=index_name, body=build_body(after_key))
        agg = resp["aggregations"][_COMPOSITE_AGG_NAME]
        buckets = agg["buckets"]
        for bucket in buckets:
            yield bucket
        if len(buckets) < page_size:
            break
        after_key = agg["after_key"]


async def get_grouped_documents(
    search: AsyncOpenSearch,
    index_name: str,
    query_clause: dict[str, Any],
    page_size: int,
    group_field: str,
    build_record: Callable[[str, dict[str, Any]], R],
    accumulate_record: Callable[[R, dict[str, Any]], None],
    sub_aggs: dict[str, Any] | None = None,
    extra_sources: list[dict[str, Any]] | None = None,
) -> dict[str, R]:
    """Aggregate matching documents into one record per distinct group_field value.

    :param search: OpenSearch client.
    :param index_name: OpenSearch index to query.
    :param query_clause: Restricts which documents are aggregated.
    :param page_size: How many buckets to request per page.
    :param group_field: The field whose distinct values become the returned
        dict's keys.
    :param build_record: Called once per group, on its first bucket, to
        create its record — typically from ``sub_aggs`` data, since a
        composite key alone rarely carries everything a record needs.
    :param accumulate_record: Called for every bucket, including that first
        one, to fold it into the record — e.g. a count, a member id.
    :param sub_aggs: Extra sub-aggregation(s) to run for every bucket. Typically
        ``top_hits_sub_agg``, fetching fields ``build_record`` needs but the
        composite bucket key itself doesn't have.
    :param extra_sources: Additional composite bucket keys beyond ``group_field``.
    :return: One record per distinct ``group_field`` value, keyed by that value.
    """
    sources: list[dict[str, Any]] = [{group_field: {"terms": {"field": group_field}}}]
    if extra_sources:
        sources += extra_sources

    groups: dict[str, R] = {}
    async for bucket in _iter_paged_buckets(
        search,
        index_name,
        page_size,
        lambda after_key: _paged_buckets_request(
            query_clause, sources, page_size, after_key, sub_aggs
        ),
    ):
        group_id = bucket["key"][group_field]
        if group_id not in groups:
            groups[group_id] = build_record(group_id, bucket)
        accumulate_record(groups[group_id], bucket)

    return groups
