"""Value counts of a keyword field, via a terms aggregation."""

from typing import Any

from opensearchpy import AsyncOpenSearch

from search_api.exceptions import SystemException

# Names the reverse_nested aggregation that counts documents rather than group items.
_DOCUMENTS_AGG_NAME = "documents"

_MAX_KEYWORD_VALUES = 10000  # Upper bound on distinct values for one field.


def _keyword_aggregation_request(
    field_name: str,
    *,
    document_filter: dict[str, Any] | None = None,
    group_item_filter: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build OpenSearch request body for keyword aggregation.

    Example for ``field_name="diagnosis.diagnosis"``, called with
    ``document_filter={"term": {"scope": "clinical"}}`` and
    ``group_item_filter={"terms": {"diagnosis.qualifiers": ["observation:confirmed"]}}``::

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

    :param field_name: Full dotted field path.
    :param document_filter: Restricts which documents are included.
    :param group_item_filter: Restricts which of a group's items within those
        documents are included. Nested fields only.
    :return: The OpenSearch request body.
    :raises SystemException: If group_item_filter is given for a field not in a group.
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
    return body


def _keyword_aggregation_counts(
    response: dict[str, Any],
    field_name: str,
    group_item_filter: dict[str, Any] | None = None,
) -> dict[str, int]:
    """Parse a response to a keyword aggregation request.

    The response nests using the same aggregation names as the request::

        {"aggregations": {
          "group_items": {
           "qualified_items": {
            "field_values": {
             "buckets": [{"key": "73211009",
                          "doc_count": 2,               # matching items
                          "documents": {"doc_count": 1}}]}}}}}

        ->  {"73211009": 1}

    A bucket's own ``doc_count`` counts group items, so one document holding two
    matching items would count twice for the same value; the reverse_nested
    aggregation's count, read instead for a nested field, climbs back to the
    documents holding them.

    :param response: The raw OpenSearch response.
    :param field_name: The same field_name passed to _keyword_aggregation_request.
    :param group_item_filter: The same group_item_filter passed to
        _keyword_aggregation_request.
    :return: Mapping of keyword value to the number of documents carrying it.
    """
    is_nested = bool(field_name.partition(".")[1])
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


async def fetch_indexed_keywords(
    search: AsyncOpenSearch,
    index_name: str,
    field_name: str,
    *,
    document_filter: dict[str, Any] | None = None,
    group_item_filter: dict[str, Any] | None = None,
) -> dict[str, int]:
    """Return each distinct value of a keyword field with its count.

    :param search: OpenSearch client.
    :param index_name: OpenSearch index to query.
    :param field_name: Full dotted field path.
    :param document_filter: Restricts which documents are included.
    :param group_item_filter: Restricts which of a group's items within those
        documents are included. Nested fields only.
    :return: Mapping of keyword value to the number of documents carrying it.
    """
    body = _keyword_aggregation_request(
        field_name,
        document_filter=document_filter,
        group_item_filter=group_item_filter,
    )
    response = await search.search(index=index_name, body=body)
    return _keyword_aggregation_counts(response, field_name, group_item_filter)
