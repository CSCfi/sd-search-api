"""Integration tests for the AI search endpoint. Requires Ollama running locally."""

import httpx
import pytest

from search_api.ai.models import AISearchResponse
from search_api.api.beacon.models import BeaconQueryFilter
from search_api.api.bigpicture.models import (
    BigpictureBeaconDatasetResultSetsResponse,
    BigpictureBeaconImageResultSetsResponse,
)
from tests.integration.login import login

skip = pytest.mark.skip(reason="Requires Ollama")


@pytest.fixture(scope="module")
def client() -> httpx.Client:
    with httpx.Client(base_url="http://localhost:8000", follow_redirects=False) as c:
        login(c)
        yield c


@skip
def test_ai_datasets_query_returns_result(client: httpx.Client):
    resp = client.post(
        "/ai/datasets", json={"query": "images for human females"}, timeout=60.0
    )
    assert resp.status_code == 200
    result = AISearchResponse[BigpictureBeaconDatasetResultSetsResponse].model_validate(
        resp.json()
    )
    assert len(result.interpretation) > 0

    assert result.result.responseSummary.numTotalResults == 1
    [result_set] = result.result.response.resultSet
    [dataset] = result_set.results
    assert dataset.datasetId == "testDataset"
    assert dataset.datasetTitle == "testTitle"
    assert dataset.totalImageCount == 1
    assert dataset.matchingImageCount == 1
    assert len(result.filters) in (1, 2)
    assert BeaconQueryFilter(id="sex", value="Female") in result.filters
    if len(result.filters) == 2:
        assert BeaconQueryFilter(id="animal_species", value="human") in result.filters


@skip
def test_ai_datasets_query_missing_body_returns_422(client: httpx.Client):
    resp = client.post("/ai/datasets", json={})
    assert resp.status_code == 422


@skip
def test_ai_images_query_returns_result(client: httpx.Client):
    resp = client.post(
        "/ai/images", json={"query": "images for human females"}, timeout=60.0
    )
    assert resp.status_code == 200
    result = AISearchResponse[BigpictureBeaconImageResultSetsResponse].model_validate(
        resp.json()
    )
    assert len(result.interpretation) > 0
    images = [r for rs in result.result.response.resultSet for r in rs.results]
    assert result.result.responseSummary.numTotalResults == len(images)


@skip
def test_ai_images_query_missing_body_returns_422(client: httpx.Client):
    resp = client.post("/ai/images", json={})
    assert resp.status_code == 422
