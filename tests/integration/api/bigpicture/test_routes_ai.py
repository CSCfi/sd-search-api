"""Integration tests for the AI search endpoint. Requires Ollama running locally."""

import httpx
import pytest

from search_api.ai.models import (
    AIDatasetResult,
    AIQueryFilter,
    AISearchResult,
)

skip = pytest.mark.skip(reason="Requires Ollama")


@pytest.fixture(scope="module")
def client() -> httpx.Client:
    with httpx.Client(base_url="http://localhost:8000") as c:
        yield c


@skip
def test_ai_query_returns_result(client: httpx.Client):
    resp = client.post(
        "/ai/query", json={"query": "images for human females"}, timeout=60.0
    )
    assert resp.status_code == 200
    result = AISearchResult.model_validate(resp.json())
    assert isinstance(result.interpretation, str)
    assert len(result.interpretation) > 0
    assert result.dataset_count >= 0
    assert isinstance(result.datasets, list)

    assert result.dataset_count == 1
    assert len(result.datasets) == 1
    dataset: AIDatasetResult = result.datasets[0]
    assert dataset.dataset_id == "testDataset"
    assert dataset.dataset_title == "testTitle"
    assert dataset.total_image_count == 1
    assert dataset.matching_image_count == 1
    assert len(result.filters) in (1, 2)
    assert AIQueryFilter(id="sex", value="Female") in result.filters
    if len(result.filters) == 2:
        assert AIQueryFilter(id="animal_species", value="human") in result.filters


@skip
def test_ai_query_missing_body_returns_422(client: httpx.Client):
    resp = client.post("/ai/query", json={})
    assert resp.status_code == 422
