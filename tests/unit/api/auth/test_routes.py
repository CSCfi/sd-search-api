"""Unit tests for auth API routes."""

import os
from unittest.mock import AsyncMock, MagicMock

os.environ["BASE_URL"] = "http://localhost:8000"
os.environ["OIDC_URL"] = "http://localhost:9999"
os.environ["OIDC_CLIENT_ID"] = "test-client-id"
os.environ["OIDC_CLIENT_SECRET"] = "test-client-secret"

import pytest
from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.testclient import TestClient

from search_api.api.auth.routes import OIDC_STATE_COOKIE, get_auth_service, router
from search_api.api.exception_handlers import register_exception_handlers
from search_api.exceptions import SystemException
from search_api.services.auth import AuthServiceHandler, RefreshRejected, TokenSet

TOKENS = TokenSet(access_token="at", expires_in=3599, refresh_token="rt")

app = FastAPI()
app.include_router(router)
register_exception_handlers(app)


@pytest.fixture
def auth_service():
    service = MagicMock()
    service.get_oidc_auth_url = AsyncMock(
        return_value="https://idp.example/auth?state=abc123"
    )
    service.callback = AsyncMock(return_value=TOKENS)
    service.initiate_web_session = MagicMock(
        return_value=RedirectResponse(url="/docs", status_code=303)
    )
    service.logout = MagicMock(
        return_value=RedirectResponse(url="/docs", status_code=303)
    )
    app.dependency_overrides[get_auth_service] = lambda: service
    return service


@pytest.fixture
def client():
    # https base_url so the Secure oidc_state cookie set by /login is actually sent
    # back by the client on the follow-up /callback request in these tests.
    with TestClient(app, base_url="https://testserver", follow_redirects=False) as c:
        yield c


def test_login_redirects_to_oidc_auth_url(auth_service, client):
    resp = client.get("/login")
    assert resp.status_code == 303
    assert resp.headers["location"] == "https://idp.example/auth?state=abc123"
    auth_service.get_oidc_auth_url.assert_awaited_once_with()

    set_cookie = resp.headers["set-cookie"]
    assert f"{OIDC_STATE_COOKIE}=abc123" in set_cookie
    assert "HttpOnly" in set_cookie
    assert "SameSite=lax" in set_cookie
    assert "Path=/callback" in set_cookie


def test_callback_initiates_web_session(auth_service, client):
    client.get("/login")

    resp = client.get("/callback", params={"state": "abc123", "code": "c"})
    auth_service.callback.assert_awaited_once_with("abc123", "c")
    auth_service.initiate_web_session.assert_called_once_with(TOKENS)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/docs"


def test_callback_without_state_cookie_returns_401_and_skips_callback(
    auth_service, client
):
    resp = client.get("/callback", params={"state": "abc123", "code": "c"})

    assert resp.status_code == 401
    auth_service.callback.assert_not_awaited()


def test_callback_with_mismatched_state_returns_401_and_skips_callback(
    auth_service, client
):
    client.get("/login")

    resp = client.get("/callback", params={"state": "someone-elses-state", "code": "c"})

    assert resp.status_code == 401
    auth_service.callback.assert_not_awaited()


def test_logout_calls_logout(auth_service, client):
    resp = client.get("/logout")
    auth_service.logout.assert_called_once_with()
    assert resp.status_code == 303
    assert resp.headers["location"] == "/docs"


@pytest.fixture
def refreshing_service():
    """A real handler, so the cookies are the ones it sets; only the IdP is mocked."""
    service = AuthServiceHandler()
    service.refresh = AsyncMock(  # type: ignore[method-assign]
        return_value=TokenSet(
            access_token="new-at", expires_in=3599, refresh_token="new-rt"
        )
    )
    app.dependency_overrides[get_auth_service] = lambda: service
    return service


def test_refresh_replaces_both_cookies(refreshing_service, client):
    client.cookies.set("refresh_token", "old-rt", path="/refresh")

    resp = client.post("/refresh")

    assert resp.status_code == 204
    refreshing_service.refresh.assert_awaited_once_with("old-rt")
    assert resp.cookies.get("access_token") == "new-at"
    assert resp.cookies.get("refresh_token") == "new-rt"
    assert resp.headers["cache-control"] == "no-cache, no-store, must-revalidate"


def test_refresh_without_cookie_returns_401_and_skips_provider(
    refreshing_service, client
):
    resp = client.post("/refresh")

    assert resp.status_code == 401
    refreshing_service.refresh.assert_not_awaited()


def test_refresh_rejected_returns_401_and_clears_cookies(refreshing_service, client):
    refreshing_service.refresh.side_effect = RefreshRejected("invalid_grant")
    client.cookies.set("refresh_token", "spent-rt", path="/refresh")

    resp = client.post("/refresh")

    assert resp.status_code == 401
    set_cookies = resp.headers.get_list("set-cookie")
    assert any(c.startswith("access_token=") and "Max-Age=0" in c for c in set_cookies)
    assert any(
        c.startswith("refresh_token=") and "Max-Age=0" in c and "Path=/refresh" in c
        for c in set_cookies
    )


def test_refresh_provider_unreachable_returns_503_and_keeps_cookies(
    refreshing_service, client
):
    refreshing_service.refresh.side_effect = SystemException("unreachable")
    client.cookies.set("refresh_token", "rt", path="/refresh")

    resp = client.post("/refresh")

    assert resp.status_code == 503
    assert "set-cookie" not in resp.headers


def test_refresh_only_accepts_post(refreshing_service, client):
    assert client.get("/refresh").status_code == 405
