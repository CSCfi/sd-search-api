"""Integration tests for the AI search endpoint. Requires Ollama running locally."""

import pytest
from fastapi.testclient import TestClient
from search_api.api.bigpicture.routes import get_beacon_service
from search_api.api.bigpicture.services.ai import (
    AISearchResult,
    AIDatasetResult,
    AIQueryFilter,
)
from search_api.api.beacon.services import MockBeaconService
from search_api.api.bigpicture.models import BP_FILTERING_TERMS
from search_api.main import app

skip = pytest.mark.skip(reason="Requires Ollama")


def mock_beacon_service():
    return MockBeaconService(BP_FILTERING_TERMS)


app.dependency_overrides[get_beacon_service] = mock_beacon_service


@skip
def test_ai_query_returns_result():
    resp = TestClient(app).post(
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
def test_ai_query_missing_body_returns_422():
    resp = TestClient(app).post("/ai/query", json={})
    assert resp.status_code == 422
