"""Unit tests for search_api.services.oidc_metadata."""

import asyncio
import os

os.environ["BASE_URL"] = "http://localhost:8000"
os.environ["OIDC_URL"] = "http://localhost:9999"
os.environ["OIDC_CLIENT_ID"] = "test-client-id"
os.environ["OIDC_CLIENT_SECRET"] = "test-client-secret"

import httpx
import pytest

from search_api.exceptions import SystemException
from search_api.services import oidc_metadata
from search_api.services.oidc_metadata import ProviderMetadata

DOCUMENT = {
    "issuer": "https://issuer.example/oidc/",
    "jwks_uri": "https://issuer.example/oidc/jwk",
    "token_endpoint": "https://issuer.example/oidc/token",
}


@pytest.fixture
def issuer(monkeypatch):
    """Answer the discovery request with the next entry of `responses`."""
    monkeypatch.setenv("OIDC_URL", "https://issuer.example/oidc/")
    state: dict = {"responses": [], "requests": []}

    def handler(request: httpx.Request) -> httpx.Response:
        state["requests"].append(str(request.url))
        response = state["responses"].pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    real_client = httpx.AsyncClient

    def client(**kwargs):
        return real_client(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(oidc_metadata.httpx, "AsyncClient", client)
    return state


@pytest.mark.asyncio
async def test_document_is_fetched_once_and_kept(issuer):
    issuer["responses"] = [httpx.Response(200, json=DOCUMENT)]
    metadata = ProviderMetadata()

    assert await metadata.get() == DOCUMENT
    assert await metadata.get() == DOCUMENT
    assert issuer["requests"] == [
        "https://issuer.example/oidc/.well-known/openid-configuration"
    ]


@pytest.mark.asyncio
async def test_concurrent_first_requests_fetch_once(issuer):
    issuer["responses"] = [httpx.Response(200, json=DOCUMENT)]
    metadata = ProviderMetadata()

    results = await asyncio.gather(*(metadata.get() for _ in range(5)))

    assert results == [DOCUMENT] * 5
    assert len(issuer["requests"]) == 1


@pytest.mark.asyncio
async def test_failed_fetch_is_not_kept(issuer):
    issuer["responses"] = [
        httpx.ConnectError("unreachable"),
        httpx.Response(200, json=DOCUMENT),
    ]
    metadata = ProviderMetadata()

    with pytest.raises(SystemException):
        await metadata.get()
    assert await metadata.get() == DOCUMENT


@pytest.mark.asyncio
async def test_error_status_raises_system_exception(issuer):
    issuer["responses"] = [httpx.Response(500)]
    with pytest.raises(SystemException):
        await ProviderMetadata().get()


@pytest.mark.asyncio
async def test_document_missing_endpoints_is_refused(issuer):
    issuer["responses"] = [httpx.Response(200, json={"issuer": DOCUMENT["issuer"]})]
    with pytest.raises(SystemException):
        await ProviderMetadata().get()
