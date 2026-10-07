"""Unit tests for the AI filters route, with the model stubbed."""

from unittest.mock import AsyncMock, sentinel

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from search_api.ai.models import AIInterpretation
from search_api.ai.services import AIService
from search_api.api.beacon.models import BeaconQueryFilter
from search_api.api.beacon.routes import (
    get_beacon_service,
    get_ontology_term_services,
    make_beacon_router,
)
from search_api.api.bigpicture.domain import BP_DOMAIN
from search_api.api.exception_handlers import register_exception_handlers
from search_api.exceptions import SystemException

QUERY = "images of females"
INTERPRETATION = AIInterpretation(
    interpretation="Female images.",
    filters=[BeaconQueryFilter(id="sex", value="Female")],
)


@pytest.fixture
def interpret(monkeypatch) -> AsyncMock:
    interpret = AsyncMock(return_value=INTERPRETATION)
    monkeypatch.setattr(AIService, "interpret", interpret)
    return interpret


@pytest.fixture
def client(monkeypatch, interpret) -> TestClient:
    # The router adds the AI routes only if FEATURE_AI is set when it is built.
    monkeypatch.setenv("FEATURE_AI", "true")
    monkeypatch.setenv("LLM_BASE_URL", "http://localhost/v1")
    monkeypatch.setenv("LLM_API_KEY", "test")

    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(make_beacon_router(BP_DOMAIN))
    # Nothing in these tests uses the beacon service or term caches. The route
    # only passes them on to interpret, which is mocked. So the beacon service
    # or term cache should not be used, and this is checked by using sentinels.
    app.dependency_overrides[get_beacon_service] = lambda: sentinel.beacon_service
    app.dependency_overrides[get_ontology_term_services] = lambda: sentinel.term_caches
    return TestClient(app)


def test_ai_filters_returns_interpretation(client, interpret):
    resp = client.post("/ai/filters", json={"query": QUERY})
    assert resp.status_code == 200
    assert AIInterpretation.model_validate(resp.json()) == INTERPRETATION
    interpret.assert_awaited_once_with(
        QUERY, sentinel.beacon_service, sentinel.term_caches, None
    )


def test_ai_filters_passes_scope(client, interpret):
    scope = BP_DOMAIN.filtering_scopes[0].id
    resp = client.post("/ai/filters", json={"query": QUERY, "requestedScope": scope})
    assert resp.status_code == 200
    interpret.assert_awaited_once_with(
        QUERY, sentinel.beacon_service, sentinel.term_caches, scope
    )


def test_ai_filters_invalid_scope(client, interpret):
    resp = client.post(
        "/ai/filters", json={"query": QUERY, "requestedScope": "invalid"}
    )
    assert resp.status_code == 400
    interpret.assert_not_awaited()


def test_ai_filters_missing_query(client, interpret):
    resp = client.post("/ai/filters", json={})
    assert resp.status_code == 422
    interpret.assert_not_awaited()


def test_ai_filters_system_exception(client, interpret):
    interpret.side_effect = SystemException("AI service error.")
    resp = client.post("/ai/filters", json={"query": QUERY})
    assert resp.status_code == 503
