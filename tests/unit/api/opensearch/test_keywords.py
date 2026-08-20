import pytest

from search_api.exceptions import SystemException
from search_api.api.opensearch.keywords import _keyword_aggregation_request


def test_keyword_aggregation_request_reverse_nested():
    body = _keyword_aggregation_request("diagnosis.diagnosis")

    field_values = body["aggs"]["group_items"]["aggs"]["field_values"]
    assert field_values["aggs"] == {"documents": {"reverse_nested": {}}}


def test_keyword_aggregation_request_group_item_filter_without_group():
    with pytest.raises(
        SystemException, match="Cannot filter the group items of 'dataset_title'"
    ):
        _keyword_aggregation_request(
            "dataset_title",
            group_item_filter={"bool": {"filter": []}},
        )
