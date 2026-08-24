"""Value counts of a keyword field, via a paged composite aggregation."""

from typing import Any

from opensearchpy import AsyncOpenSearch

# Names the reverse_nested aggregation that counts documents rather than group items.
_DOCUMENTS_AGG_NAME = "documents"

# Distinct values requested per page.
_KEYWORD_PAGE_SIZE = 10000


def _keyword_aggregation_request(
    field_name: str,
    page_size: int,
    after_key: dict[str, Any] | None = None,
    *,
    document_filter: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build OpenSearch request body for one page of a keyword aggregation.

    Example for ``field_name="observation.diagnosis"``, called with
    ``document_filter={"term": {"scope": "clinical"}}``::

        {"size": 0,                                    # aggregations only, no hits
         "query": {"term": {"scope": "clinical"}},     # document_filter: which documents
         "aggs": {"group_items": {                     # count over the observation items
           "nested": {"path": "observation"},
           "aggs": {"field_values": {                  # one bucket per diagnosis value
             "composite": {"size": 10000,
               "sources": [{"value": {"terms": {"field": "observation.diagnosis"}}}]},
             "aggs": {"documents": {                    # count documents, not items
               "reverse_nested": {}}}}}}}}

    :param field_name: Full dotted field path.
    :param page_size: How many distinct values to request per page.
    :param after_key: The previous page's after_key, to advance to the next
        page, or None for the first page.
    :param document_filter: Restricts which documents are included.
    :return: The OpenSearch request body.
    """
    nested_path, separator, _ = field_name.partition(".")
    is_nested = bool(separator)

    # Aggregations are built from the inside out. A bucket's own doc_count counts
    # group items, so for a field in a group reverse_nested climbs back to the
    # documents holding those items and counts documents instead.
    composite: dict[str, Any] = {
        "size": page_size,
        "sources": [{"value": {"terms": {"field": field_name}}}],
    }
    if after_key:
        composite["after"] = after_key
    field_values: dict[str, Any] = {"composite": composite}
    if is_nested:
        field_values["aggs"] = {_DOCUMENTS_AGG_NAME: {"reverse_nested": {}}}
    aggregations: dict[str, Any] = {"field_values": field_values}
    if is_nested:
        aggregations = {
            "group_items": {"nested": {"path": nested_path}, "aggs": aggregations}
        }

    # "size": 0 discards the matching hits, leaving only the aggregations.
    body: dict[str, Any] = {"size": 0, "aggs": aggregations}
    if document_filter is not None:
        body["query"] = document_filter
    return body


def _keyword_aggregation_counts(
    response: dict[str, Any],
    field_name: str,
) -> tuple[dict[str, int], dict[str, Any] | None]:
    """Parse a response to a keyword aggregation request.

    The response nests using the same aggregation names as the request::

        {"aggregations": {
          "group_items": {
           "field_values": {
            "after_key": {"value": "73211009"},
            "buckets": [{"key": {"value": "73211009"},
                         "doc_count": 2,               # matching items
                         "documents": {"doc_count": 1}}]}}}}

        ->  ({"73211009": 1}, {"value": "73211009"})

    A bucket's own ``doc_count`` counts group items, so one document holding two
    matching items would count twice for the same value; the reverse_nested
    aggregation's count, read instead for a nested field, climbs back to the
    documents holding them.

    :param response: The raw OpenSearch response.
    :param field_name: The same field_name passed to _keyword_aggregation_request.
    :return: This page's value counts, and the after_key to advance to the
        next page (or None if this was the last page).
    """
    is_nested = bool(field_name.partition(".")[1])
    result = response["aggregations"]
    if is_nested:
        result = result["group_items"]

    field_values = result["field_values"]
    counts = {
        bucket["key"]["value"]: (
            bucket[_DOCUMENTS_AGG_NAME]["doc_count"]
            if is_nested
            else bucket["doc_count"]
        )
        for bucket in field_values["buckets"]
    }
    return counts, field_values.get("after_key")


async def fetch_indexed_keywords(
    search: AsyncOpenSearch,
    index_name: str,
    field_name: str,
    *,
    document_filter: dict[str, Any] | None = None,
) -> dict[str, int]:
    """Return every distinct value of a keyword field with its count, however
    many there are. Pages via a composite aggregation.

    :param search: OpenSearch client.
    :param index_name: OpenSearch index to query.
    :param field_name: Full dotted field path.
    :param document_filter: Restricts which documents are included.
    :return: Mapping of keyword value to the number of documents carrying it.
    """
    counts: dict[str, int] = {}
    after_key: dict[str, Any] | None = None
    while True:
        body = _keyword_aggregation_request(
            field_name,
            _KEYWORD_PAGE_SIZE,
            after_key,
            document_filter=document_filter,
        )
        response = await search.search(index=index_name, body=body)
        page_counts, after_key = _keyword_aggregation_counts(response, field_name)
        counts.update(page_counts)
        if len(page_counts) < _KEYWORD_PAGE_SIZE:
            break
    return counts
