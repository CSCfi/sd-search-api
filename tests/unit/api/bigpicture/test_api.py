import json

from fastapi.testclient import TestClient

from search_api.api.beacon.models import (
    BeaconQueryRequest,
    BeaconQuery,
    BeaconBooleanResponse,
    BeaconCountResponse,
    BeaconResultSetsResponse,
)
from search_api.api.bigpicture.models import (
    BP_INFO_RESPONSE,
    BP_FILTERING_TERMS_RESPONSE,
)
from search_api.main import app
from search_api.api.bigpicture.routes import get_service
from search_api.api.bigpicture.services import (
    MockBigpictureBeaconService,
    get_mock_query_result,
)


def get_mock_service():
    return MockBigpictureBeaconService()


app.dependency_overrides[get_service] = get_mock_service


def test_query_dataset_mock_service():
    # boolean granularity
    request = BeaconQueryRequest(query=BeaconQuery(requestedGranularity="boolean"))
    client = TestClient(app)
    resp = client.post("/query", json=request.model_dump())
    assert resp.status_code == 200
    response = BeaconBooleanResponse.model_validate(resp.json())
    assert response.responseSummary.exists

    # count granularity
    request = BeaconQueryRequest(query=BeaconQuery(requestedGranularity="count"))
    client = TestClient(app)
    resp = client.post("/query", json=request.model_dump())
    assert resp.status_code == 200
    response = BeaconCountResponse.model_validate(resp.json())
    assert response.responseSummary.exists
    assert response.responseSummary.numTotalResults == 1

    # record granularity
    request = BeaconQueryRequest(query=BeaconQuery(requestedGranularity="record"))
    client = TestClient(app)
    resp = client.post("/query", json=request.model_dump())
    assert resp.status_code == 200
    response = BeaconResultSetsResponse.model_validate(resp.json())
    assert response.responseSummary.exists
    assert response.responseSummary.numTotalResults == 1
    assert response.response.resultSet == get_mock_query_result().resultSet


def test_info_endpoint():
    client = TestClient(app)
    response = client.get("/info")

    assert response.status_code == 200
    data = response.json()
    assert json.dumps(data) == json.dumps(
        BP_INFO_RESPONSE.model_dump(exclude_none=True)
    )


def test_filtering_terms_endpoint():
    client = TestClient(app)
    response = client.get("/filtering_terms")

    assert response.status_code == 200
    data = response.json()
    assert json.dumps(data) == json.dumps(
        BP_FILTERING_TERMS_RESPONSE.model_dump(exclude_none=True)
    )
