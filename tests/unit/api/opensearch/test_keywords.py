from search_api.api.opensearch.keywords import _keyword_aggregation_request


def test_keyword_aggregation_request_reverse_nested():
    body = _keyword_aggregation_request("observation.diagnosis", 100)

    field_values = body["aggs"]["group_items"]["aggs"]["field_values"]
    assert field_values["aggs"] == {"documents": {"reverse_nested": {}}}
    assert field_values["composite"]["size"] == 100
    assert "after" not in field_values["composite"]


def test_keyword_aggregation_request_after_key():
    body = _keyword_aggregation_request("dataset_title", 100, {"value": "a"})

    assert body["aggs"]["field_values"]["composite"]["after"] == {"value": "a"}
