"""Unit tests for admin API routes."""

import os
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from search_api.api.admin.routes import router
from search_api.api.beacon.models import SNOMED_ONTOLOGY_ID
from search_api.api.bigpicture.models import BP_FILTERING_TERMS
from search_api.api.exception_handlers import register_exception_handlers
from search_api.api.models import FieldValue, ValueCounts
from search_api.services.ontology.snomed import SnomedService

_ADMIN_KEY = "test-admin-key"
os.environ["ADMIN_KEY"] = _ADMIN_KEY

app = FastAPI()
app.include_router(router)
app.state.filtering_terms = BP_FILTERING_TERMS
register_exception_handlers(app)


@pytest.fixture
def snomed_term_service():
    service = MagicMock()
    service.load = AsyncMock()
    service.refresh = AsyncMock()
    service.get_preferred_terms = AsyncMock(return_value={})
    app.state.ontology_term_services = {SNOMED_ONTOLOGY_ID: service}
    return service


@pytest.fixture
def beacon_service():
    service = MagicMock()
    service.get_value_counts = AsyncMock(return_value=ValueCounts(counts={}))
    app.state.beacon_service = service
    return service


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def _auth(key: str = _ADMIN_KEY) -> dict:
    return {"Authorization": f"Bearer {key}"}


def test_reload_calls_load(snomed_term_service, client):
    resp = client.post("/admin/snomed/reload", headers=_auth())
    assert resp.status_code == 204
    snomed_term_service.load.assert_called_once_with()


def test_refresh_calls_refresh(snomed_term_service, client):
    resp = client.post("/admin/snomed/refresh", headers=_auth())
    assert resp.status_code == 204
    snomed_term_service.refresh.assert_called_once()
    args, _ = snomed_term_service.refresh.call_args
    assert isinstance(args[0], SnomedService)


def test_reload_rejects_wrong_key(snomed_term_service, client):
    resp = client.post("/admin/snomed/reload", headers=_auth("wrong-key"))
    assert resp.status_code == 403


def test_reload_requires_auth_header(snomed_term_service, client):
    resp = client.post("/admin/snomed/reload")
    assert resp.status_code == 401


def test_refresh_rejects_wrong_key(snomed_term_service, client):
    resp = client.post("/admin/snomed/refresh", headers=_auth("wrong-key"))
    assert resp.status_code == 403


def test_refresh_requires_auth_header(snomed_term_service, client):
    resp = client.post("/admin/snomed/refresh")
    assert resp.status_code == 401


def test_invalid_concepts_unknown_field(beacon_service, client):
    resp = client.get("/admin/snomed/fields/unknown/invalid_concepts", headers=_auth())
    assert resp.status_code == 400
    assert resp.json()["detail"] == "Unknown field: 'unknown'."


def test_invalid_concepts_non_ontology_field(beacon_service, client):
    resp = client.get(
        "/admin/snomed/fields/dataset_title/invalid_concepts", headers=_auth()
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == (
        "Concept validation is not supported for field 'dataset_title' (type 'text')."
    )


def test_invalid_concepts(beacon_service, client):
    beacon_service.get_value_counts = AsyncMock(
        return_value=ValueCounts(counts={"410607006": 10, "invalid1": 6, "invalid2": 2})
    )
    resp = client.get(
        "/admin/snomed/fields/animal_species/invalid_concepts", headers=_auth()
    )
    assert resp.status_code == 200
    assert resp.json() == [
        FieldValue(value="invalid1", count=6).model_dump(),
        FieldValue(value="invalid2", count=2).model_dump(),
    ]


def test_unexpected_concepts_unknown_field(client):
    resp = client.get(
        "/admin/snomed/fields/invalid/unexpected_concepts", headers=_auth()
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "Unknown field: 'invalid'."


def test_unexpected_concepts_non_ontology_field(client):
    resp = client.get(
        "/admin/snomed/fields/dataset_title/unexpected_concepts", headers=_auth()
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == (
        "Concept validation is not supported for field 'dataset_title' (type 'text')."
    )


def test_unexpected_concepts(beacon_service, snomed_term_service, client):
    beacon_service.get_value_counts = AsyncMock(
        return_value=ValueCounts(
            counts={"410607006": 10, "999999999": 3, "invalid1": 6}
        )
    )
    snomed_term_service.get_preferred_terms = AsyncMock(
        return_value={"410607006": "Homo sapiens"}
    )
    resp = client.get(
        "/admin/snomed/fields/animal_species/unexpected_concepts", headers=_auth()
    )
    assert resp.status_code == 200
    assert resp.json() == [
        FieldValue(value="999999999", count=3).model_dump(),
    ]
