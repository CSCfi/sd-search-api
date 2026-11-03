"""Integration tests for the AI filters endpoint."""

import os

import httpx
import pytest

from search_api.ai.models import AIInterpretation
from search_api.api.beacon.models import BeaconQueryFilter
from search_api.conf import feature_config
from tests.integration.login import login

requires_feature_ai = pytest.mark.skipif(
    not feature_config().FEATURE_AI, reason="Requires FEATURE_AI=true"
)

# Longer than the server's LLM_TIMEOUT. A model that is too slow then fails the
# test with a 503, not with a client timeout.
_TIMEOUT = float(os.environ.get("LLM_TIMEOUT", "60")) + 30


@pytest.fixture(scope="module")
def client() -> httpx.Client:
    with httpx.Client(base_url="http://localhost:8000", follow_redirects=False) as c:
        login(c)
        yield c


@requires_feature_ai
def test_ai_filters_returns_filters(client: httpx.Client):
    resp = client.post(
        "/ai/filters", json={"query": "images for human females"}, timeout=_TIMEOUT
    )
    assert resp.status_code == 200
    result = AIInterpretation.model_validate(resp.json())
    assert len(result.interpretation) > 0
    assert len(result.filters) in (1, 2)
    assert BeaconQueryFilter(id="sex", value=["Female"]) in result.filters
    if len(result.filters) == 2:
        # Ontology values are returned as concept ids: Homo sapiens.
        assert (
            BeaconQueryFilter(id="animal_species", value=["337915000"])
            in result.filters
        )


@requires_feature_ai
def test_ai_filters_removes_current_filter(client: httpx.Client):
    # The query removes the only current filter. The response returns every
    # filter after that change, so it has none.
    resp = client.post(
        "/ai/filters",
        json={
            "query": "Remove sex",
            "filters": [{"id": "sex", "value": "Female", "operator": "="}],
        },
        timeout=_TIMEOUT,
    )
    assert resp.status_code == 200
    result = AIInterpretation.model_validate(resp.json())
    assert result.answerable
    assert result.filters == []


@requires_feature_ai
def test_ai_filters_missing_body_returns_422(client: httpx.Client):
    resp = client.post("/ai/filters", json={})
    assert resp.status_code == 422
