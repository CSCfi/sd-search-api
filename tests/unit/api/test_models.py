"""The models shared by the API layer."""

import pytest
from pydantic import ValidationError

from search_api.api.beacon.models import MAX_FILTERS, MAX_ID_LENGTH
from search_api.api.models import MAX_AI_QUERY_LENGTH, AIQueryRequest, ValueCountsKey


def test_value_counts_key_distinguishes_scope():
    unrestricted = ValueCountsKey.of("sex")

    assert unrestricted != ValueCountsKey.of("sex", scope="clinical")
    assert unrestricted == ValueCountsKey.of("sex", scope=None)


def test_value_counts_key_cannot_be_changed():
    key = ValueCountsKey.of("sex")

    with pytest.raises(ValueError):
        key.field_id = "diagnosis"


def test_ai_query_at_limit_is_accepted():
    query = "q" * MAX_AI_QUERY_LENGTH
    assert AIQueryRequest(query=query).query == query


@pytest.mark.parametrize(
    "request_body",
    [
        {"query": "q" * (MAX_AI_QUERY_LENGTH + 1)},
        {"query": "q", "filters": [{"id": "id", "value": "v"}] * (MAX_FILTERS + 1)},
        {"query": "q", "requestedScope": "x" * (MAX_ID_LENGTH + 1)},
    ],
    ids=["query", "filters", "scope"],
)
def test_ai_query_over_limit_is_rejected(request_body):
    with pytest.raises(ValidationError):
        AIQueryRequest.model_validate(request_body)
