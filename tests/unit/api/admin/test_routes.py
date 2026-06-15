"""Unit tests for admin API routes."""

import os
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from search_api.api.admin.routes import router
from search_api.services.snomed import SnomedService

_ADMIN_KEY = "test-admin-key"
os.environ["ADMIN_KEY"] = _ADMIN_KEY

app = FastAPI()
app.include_router(router)


@pytest.fixture
def snomed_term_service():
    service = MagicMock()
    service.load = AsyncMock()
    service.refresh = AsyncMock()
    app.state.snomed_term_service = service
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
