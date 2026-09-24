"""Unit tests for the AI query routes, with the model and the search stubbed."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from search_api.ai.models import AIInterpretation
from search_api.ai.services import AIService
from search_api.api.beacon.models import (
    BeaconQueryFilter,
    BeaconResultSet,
    BeaconResultSets,
)
from search_api.api.beacon.routes import (
    get_beacon_query_services,
    get_ontology_term_services,
    make_beacon_router,
)
from search_api.api.beacon.services import BeaconQueryResult
from search_api.api.bigpicture.domain import BP_DOMAIN
from search_api.api.bigpicture.models import BigpictureBeaconImageResult
from search_api.api.exception_handlers import register_exception_handlers
from search_api.exceptions import SystemException

FILTERS = [BeaconQueryFilter(id="sex", value="Female")]


class MockImageService:
    """Finds one image, and records what was asked."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def query(self, filters, granularity="record", scope=None):
        self.calls.append(dict(filters=filters, granularity=granularity, scope=scope))
        result = BigpictureBeaconImageResult(imageId="image1")
        return BeaconQueryResult(
            total=1,
            result_sets=BeaconResultSets(
                resultSet=[BeaconResultSet(id="dataset1", results=[result])]
            ),
        )


@pytest.fixture
def service() -> MockImageService:
    return MockImageService()


@pytest.fixture
def client(monkeypatch, service) -> TestClient:
    # The router adds the AI routes only if FEATURE_AI is set when it is built.
    monkeypatch.setenv("FEATURE_AI", "true")
    monkeypatch.setenv("LLM_BASE_URL", "http://localhost/v1")
    monkeypatch.setenv("LLM_API_KEY", "test")

    async def interpret(self, query: str, scope=None) -> AIInterpretation:
        return AIInterpretation(interpretation="Female images.", filters=FILTERS)

    monkeypatch.setattr(AIService, "interpret", interpret)

    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(make_beacon_router(BP_DOMAIN))
    app.dependency_overrides[get_beacon_query_services] = lambda: {"/images": service}
    app.dependency_overrides[get_ontology_term_services] = lambda: {}
    return TestClient(app)


def test_ai_query_returns_records_by_default(client, service):
    resp = client.post("/ai/images", json={"query": "images of females"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["interpretation"] == "Female images."
    assert body["filters"] == [f.model_dump() for f in FILTERS]
    assert body["result"]["meta"]["returnedGranularity"] == "record"
    assert body["result"]["responseSummary"]["numTotalResults"] == 1
    [result_set] = body["result"]["response"]["resultSet"]
    assert result_set["results"] == [{"imageId": "image1"}]
    assert service.calls == [dict(filters=FILTERS, granularity="record", scope=None)]


def test_ai_query_returns_count(client, service):
    resp = client.post(
        "/ai/images",
        json={"query": "images of females", "requestedGranularity": "count"},
    )
    assert resp.status_code == 200
    result = resp.json()["result"]
    assert result["meta"]["returnedGranularity"] == "count"
    assert result["responseSummary"] == {"exists": True, "numTotalResults": 1}
    assert "response" not in result
    assert service.calls[0]["granularity"] == "count"


def test_ai_query_returns_boolean(client, service):
    resp = client.post(
        "/ai/images",
        json={"query": "images of females", "requestedGranularity": "boolean"},
    )
    assert resp.status_code == 200
    result = resp.json()["result"]
    assert result["meta"]["returnedGranularity"] == "boolean"
    assert result["responseSummary"] == {"exists": True}


def test_ai_query_passes_scope(client, service):
    scope = BP_DOMAIN.filtering_scopes[0].id
    resp = client.post(
        "/ai/images", json={"query": "images of females", "requestedScope": scope}
    )
    assert resp.status_code == 200
    assert service.calls[0]["scope"] == scope


def test_ai_query_rejects_invalid_scope(client, service):
    resp = client.post(
        "/ai/images", json={"query": "images of females", "requestedScope": "invalid"}
    )
    assert resp.status_code == 400
    assert service.calls == []


def test_ai_query_service_unavailable(client, service, monkeypatch):
    async def interpret(self, query: str, scope=None) -> AIInterpretation:
        raise SystemException("AI service error.")

    monkeypatch.setattr(AIService, "interpret", interpret)
    resp = client.post("/ai/images", json={"query": "images of females"})
    assert resp.status_code == 503
    assert service.calls == []
