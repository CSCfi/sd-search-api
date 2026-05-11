from fastapi.testclient import TestClient

from search_api.main import app
from search_api.api.bigpicture.routes import get_service
from search_api.api.bigpicture.services import (
    MockBigpictureBeaconService,
    get_mock_results_sets,
)


def _get_service():
    return MockBigpictureBeaconService()


app.dependency_overrides[get_service] = _get_service

client = TestClient(app)


def test_info_endpoint():
    response = client.get("/info")

    assert response.status_code == 200
    data = response.json()

    assert data["id"] == "csc-bp-image-beacon"  # ✅ updated
    assert data["name"] == "CSC Bigpicture Image Beacon"
    assert data["apiVersion"] == "v2.0"

    assert "organization" in data
    assert data["organization"]["name"] == "CSC"
    assert data["organization"]["url"] == "https://csc.fi"

    assert "description" in data


def test_filtering_terms_endpoint():
    response = client.get("/filtering_terms")

    assert response.status_code == 200
    data = response.json()

    assert "resources" in data
    assert isinstance(data["resources"], list)
    assert len(data["resources"]) > 0


def test_query_dataset_mock_service():
    request = {"filters": [], "limit": 10, "requestedGranularity": "count"}

    response = client.post("/query", json=request)

    assert response.status_code == 200
    data = response.json()

    assert "meta" in data
    assert "responseSummary" in data
    assert "response" in data
    result_sets = data["response"]["resultSets"]
    assert result_sets == get_mock_results_sets()
