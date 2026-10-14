"""Unit tests for search_api.services.auth.AuthServiceHandler."""

import base64
import os
import time
from unittest.mock import MagicMock
from urllib.parse import parse_qs

os.environ["BASE_URL"] = "http://localhost:8000"
os.environ["OIDC_URL"] = "http://localhost:9999"
os.environ["OIDC_CLIENT_ID"] = "test-client-id"
os.environ["OIDC_CLIENT_SECRET"] = "test-client-secret"
os.environ["OIDC_SECURE_COOKIE"] = "true"

import httpx
import jwt
import pytest
from fastapi import HTTPException
from idpyoidc.client.exception import OidcServiceError
from requests.exceptions import ConnectionError as RequestsConnectionError
from starlette.responses import Response

from search_api.exceptions import SystemException
from search_api.services import auth as auth_module
from search_api.services.access_token import InvalidAccessToken
from search_api.services.auth import AuthServiceHandler, RefreshRejected, TokenSet

TOKEN_ENDPOINT = "http://localhost:9999/token"


class FakeMetadata:
    async def get(self) -> dict:
        return {
            "issuer": "http://localhost:9999",
            "jwks_uri": "http://localhost:9999/jwks",
            "token_endpoint": TOKEN_ENDPOINT,
        }


class FakeValidator:
    """Accepts every access token, or raises `error`; records what it was shown."""

    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.seen: list[str] = []

    async def validate(self, token: str) -> str:
        self.seen.append(token)
        if self.error:
            raise self.error
        return "user-123"


def _handler_with_mock_rph(
    token_response: dict | None = None,
    validator: FakeValidator | None = None,
) -> tuple[AuthServiceHandler, MagicMock]:
    handler = AuthServiceHandler(
        FakeMetadata(),  # type: ignore[arg-type]
        validator or FakeValidator(),  # type: ignore[arg-type]
    )
    mock_rph = MagicMock()
    cstate = mock_rph.get_client_from_session_key.return_value.get_context.return_value.cstate
    cstate.get_set.return_value = token_response or {}
    handler._rph = mock_rph
    return handler, mock_rph


def _refresh_token(exp_in: int | None = 30 * 24 * 3600) -> str:
    claims = {"iss": "http://localhost:9999", "aud": "test-client-id", "jti": "r1"}
    if exp_in is not None:
        claims["exp"] = int(time.time()) + exp_in
    return jwt.encode(claims, "k" * 32, algorithm="HS256", headers={"typ": "JWT"})


def _set_cookies(response: Response) -> dict[str, str]:
    """Each Set-Cookie header, keyed by cookie name."""
    headers = [v.decode() for k, v in response.raw_headers if k == b"set-cookie"]
    return {header.split("=", 1)[0]: header for header in headers}


@pytest.fixture
def token_endpoint(monkeypatch):
    """Answer refresh grants with the next entry of `responses`, recording requests."""
    state: dict = {"responses": [], "requests": []}

    def handler(request: httpx.Request) -> httpx.Response:
        state["requests"].append(request)
        response = state["responses"].pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    real_client = httpx.AsyncClient

    def client(**kwargs):
        return real_client(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(auth_module.httpx, "AsyncClient", client)
    return state


@pytest.mark.asyncio
async def test_get_oidc_auth_url_returns_begin_result():
    handler, mock_rph = _handler_with_mock_rph()
    mock_rph.begin.return_value = "http://localhost:9999/auth?state=abc"

    url = await handler.get_oidc_auth_url()

    assert url == "http://localhost:9999/auth?state=abc"
    mock_rph.begin.assert_called_once_with("aai")


@pytest.mark.asyncio
async def test_get_oidc_auth_url_raises_system_exception_on_failure():
    handler, mock_rph = _handler_with_mock_rph()
    mock_rph.begin.side_effect = RequestsConnectionError("discovery unreachable")

    with pytest.raises(SystemException):
        await handler.get_oidc_auth_url()


def _no_session(rph: MagicMock) -> None:
    rph.get_session_information.side_effect = KeyError("unknown-state")


def _issuer_mismatch(rph: MagicMock) -> None:
    # RPHandler.finalize looks the issuer up in its own registry: a KeyError.
    rph.finalize.side_effect = KeyError("http://localhost:9999")


def _code_rejected(rph: MagicMock) -> None:
    rph.finalize.side_effect = OidcServiceError("invalid_grant")


def _error_response(rph: MagicMock) -> None:
    rph.finalize.return_value = {"state": "known-state", "error": "access_denied"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "setup", [_no_session, _issuer_mismatch, _code_rejected, _error_response]
)
async def test_callback_protocol_failure_raises_401(setup):
    handler, mock_rph = _handler_with_mock_rph()
    mock_rph.get_session_information.return_value = {"iss": "http://localhost:9999"}
    setup(mock_rph)

    with pytest.raises(HTTPException) as exc_info:
        await handler.callback("known-state", "some-code")

    assert exc_info.value.status_code == 401
    # A failed login is over too: its state does not stay in memory.
    mock_rph.clear_session.assert_called_once_with("known-state")


@pytest.mark.asyncio
async def test_callback_for_a_state_unknown_to_rphandler_still_answers_401():
    handler, mock_rph = _handler_with_mock_rph()
    mock_rph.get_session_information.side_effect = KeyError("unknown-state")
    # RPHandler raises KeyError for a state it does not have; that is not an error.
    mock_rph.clear_session.side_effect = KeyError("unknown-state")

    with pytest.raises(HTTPException) as exc_info:
        await handler.callback("unknown-state", "some-code")

    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_callback_without_access_token_raises_system_exception():
    # The provider's fault, as at refresh: a 503, not a 401.
    handler, mock_rph = _handler_with_mock_rph({"expires_in": 3599})
    mock_rph.get_session_information.return_value = {"iss": "http://localhost:9999"}
    mock_rph.finalize.return_value = {"userinfo": {"sub": "user-123"}}

    with pytest.raises(SystemException):
        await handler.callback("known-state", "some-code")


@pytest.mark.asyncio
@pytest.mark.parametrize("expires_in", ["not-a-number", None, [3600]])
async def test_unreadable_expires_in_is_taken_as_unknown(token_endpoint, expires_in):
    token_endpoint["responses"] = [
        httpx.Response(200, json={"access_token": "at", "expires_in": expires_in})
    ]
    handler, _ = _handler_with_mock_rph()

    tokens = await handler.refresh("rt")

    assert tokens.expires_in is None


@pytest.mark.asyncio
async def test_callback_finalize_connection_error_raises_system_exception():
    handler, mock_rph = _handler_with_mock_rph()
    mock_rph.get_session_information.return_value = {"iss": "http://localhost:9999"}
    mock_rph.finalize.side_effect = RequestsConnectionError(
        "token endpoint unreachable"
    )

    with pytest.raises(SystemException):
        await handler.callback("known-state", "some-code")
    mock_rph.clear_session.assert_called_once_with("known-state")


@pytest.mark.asyncio
async def test_callback_success_returns_identity_provider_tokens():
    handler, mock_rph = _handler_with_mock_rph(
        {"access_token": "at", "expires_in": 3599, "refresh_token": "rt"}
    )
    mock_rph.get_session_information.return_value = {"iss": "http://localhost:9999"}
    mock_rph.finalize.return_value = {"userinfo": {"sub": "user-123"}, "token": "at"}

    tokens = await handler.callback("known-state", "good-code")

    assert tokens == TokenSet(access_token="at", expires_in=3599, refresh_token="rt")
    # `code` is merged into the session info fetched from `get_session_information`
    # before being handed to `finalize`, alongside the configured issuer.
    mock_rph.finalize.assert_called_once_with(
        "http://localhost:9999", {"iss": "http://localhost:9999", "code": "good-code"}
    )
    # The finished login's state is dropped rather than kept for the process's life.
    mock_rph.clear_session.assert_called_once_with("known-state")
    # Checked as the middleware will check it, before it becomes the session.
    assert handler._validator.seen == ["at"]  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_callback_refuses_an_access_token_the_api_would_reject(caplog):
    validator = FakeValidator(InvalidAccessToken("Invalid audience"))
    handler, mock_rph = _handler_with_mock_rph(
        {"access_token": "at", "expires_in": 3599, "refresh_token": "rt"}, validator
    )
    mock_rph.get_session_information.return_value = {"iss": "http://localhost:9999"}
    mock_rph.finalize.return_value = {"userinfo": {"sub": "user-123"}, "token": "at"}

    with pytest.raises(SystemException):
        await handler.callback("known-state", "good-code")

    # A misconfiguration, logged with the reason, rather than a login that 401s.
    (record,) = [r for r in caplog.records if r.name == "search_api.services.auth"]
    assert record.levelname == "ERROR"
    assert "Invalid audience" in record.getMessage()
    mock_rph.clear_session.assert_called_once_with("known-state")


@pytest.mark.asyncio
async def test_callback_returns_fields_the_provider_left_out_as_none():
    handler, mock_rph = _handler_with_mock_rph({"access_token": "at"})
    mock_rph.get_session_information.return_value = {"iss": "http://localhost:9999"}
    mock_rph.finalize.return_value = {"userinfo": {"sub": "user-123"}, "token": "at"}

    tokens = await handler.callback("known-state", "good-code")

    assert tokens == TokenSet(access_token="at", expires_in=None, refresh_token=None)


@pytest.mark.asyncio
async def test_refresh_posts_refresh_grant_with_basic_auth(token_endpoint):
    token_endpoint["responses"] = [
        httpx.Response(
            200,
            json={
                "access_token": "new-at",
                "expires_in": 3599,
                "refresh_token": "new-rt",
            },
        )
    ]
    handler, _ = _handler_with_mock_rph()

    tokens = await handler.refresh("old-rt")

    assert tokens == TokenSet(
        access_token="new-at", expires_in=3599, refresh_token="new-rt"
    )
    (request,) = token_endpoint["requests"]
    assert str(request.url) == TOKEN_ENDPOINT
    assert parse_qs(request.content.decode()) == {
        "grant_type": ["refresh_token"],
        "refresh_token": ["old-rt"],
    }
    expected = base64.b64encode(b"test-client-id:test-client-secret").decode()
    assert request.headers["authorization"] == f"Basic {expected}"
    assert handler._validator.seen == ["new-at"]  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_refresh_refuses_an_access_token_the_api_would_reject(
    token_endpoint, caplog
):
    token_endpoint["responses"] = [
        httpx.Response(
            200,
            json={
                "access_token": "new-at",
                "expires_in": 3599,
                "refresh_token": "new-rt",
            },
        )
    ]
    validator = FakeValidator(InvalidAccessToken("Not an access token."))
    handler, _ = _handler_with_mock_rph(validator=validator)

    with pytest.raises(SystemException):
        await handler.refresh("old-rt")

    (record,) = [r for r in caplog.records if r.name == "search_api.services.auth"]
    assert record.levelname == "ERROR"
    assert "Not an access token." in record.getMessage()


@pytest.mark.asyncio
async def test_refresh_keeps_refresh_token_when_none_returned(token_endpoint):
    token_endpoint["responses"] = [
        httpx.Response(200, json={"access_token": "new-at", "expires_in": 3599})
    ]
    handler, _ = _handler_with_mock_rph()

    tokens = await handler.refresh("old-rt")

    assert tokens.refresh_token == "old-rt"


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [400, 401])
@pytest.mark.parametrize("error", ["invalid_grant", "invalid_token"])
async def test_dead_refresh_token_raises_refresh_rejected(
    token_endpoint, status, error
):
    # invalid_grant per RFC 6749; LS AAI answers a spent token with invalid_token.
    token_endpoint["responses"] = [httpx.Response(status, json={"error": error})]
    handler, _ = _handler_with_mock_rph()

    with pytest.raises(RefreshRejected):
        await handler.refresh("spent-rt")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("response", "error"),
    [
        (httpx.Response(401, json={"error": "invalid_client"}), "invalid_client"),
        (
            httpx.Response(400, json={"error": "unauthorized_client"}),
            "unauthorized_client",
        ),
        (httpx.Response(400, json={"error": "invalid_request"}), "invalid_request"),
        (httpx.Response(400, json={"error": "invalid_scope"}), "invalid_scope"),
        (
            httpx.Response(400, json={"error": "unsupported_grant_type"}),
            "unsupported_grant_type",
        ),
        (httpx.Response(400, text="not json"), "None"),
        (httpx.Response(400, json={"detail": "no error code"}), "None"),
        (httpx.Response(500), "None"),
    ],
    ids=[
        "invalid_client",
        "unauthorized_client",
        "invalid_request",
        "invalid_scope",
        "unsupported_grant_type",
        "not-json",
        "no-error-code",
        "server-error",
    ],
)
async def test_refresh_refused_for_the_client_raises_logged_system_exception(
    token_endpoint, caplog, response, error
):
    # A misconfigured client must not pass for every session expiring.
    token_endpoint["responses"] = [response]
    handler, _ = _handler_with_mock_rph()

    with (
        caplog.at_level("ERROR", logger="search_api.services.auth"),
        pytest.raises(SystemException),
    ):
        await handler.refresh("rt")

    (record,) = [r for r in caplog.records if r.name == "search_api.services.auth"]
    assert record.levelname == "ERROR"
    assert error in record.getMessage()


@pytest.mark.asyncio
async def test_refused_refresh_log_leaves_out_the_description(token_endpoint, caplog):
    token_endpoint["responses"] = [
        httpx.Response(
            401,
            json={
                "error": "invalid_client",
                "error_description": "Bad client for token secret-rt",
            },
        )
    ]
    handler, _ = _handler_with_mock_rph()

    with pytest.raises(SystemException) as exc_info:
        await handler.refresh("secret-rt")

    assert "secret-rt" not in caplog.text
    assert "secret-rt" not in str(exc_info.value)


@pytest.mark.asyncio
async def test_refresh_rejection_does_not_carry_the_token(token_endpoint):
    # LS AAI echoes the rejected refresh token in error_description.
    token_endpoint["responses"] = [
        httpx.Response(
            400,
            json={
                "error": "invalid_token",
                "error_description": "Invalid refresh token: secret-rt",
            },
        )
    ]
    handler, _ = _handler_with_mock_rph()

    with pytest.raises(RefreshRejected) as exc_info:
        await handler.refresh("secret-rt")

    assert str(exc_info.value) == "invalid_token"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, json={"token_type": "Bearer"}),
        httpx.Response(200, json=["not", "an", "object"]),
        httpx.Response(200, text="not json"),
        httpx.ConnectError("unreachable"),
    ],
)
async def test_refresh_provider_failure_raises_system_exception(
    token_endpoint, response
):
    token_endpoint["responses"] = [response]
    handler, _ = _handler_with_mock_rph()

    with pytest.raises(SystemException):
        await handler.refresh("rt")


def test_initiate_web_session_sets_both_cookies_and_redirects():
    handler, _ = _handler_with_mock_rph()
    refresh_token = _refresh_token()

    response = handler.initiate_web_session(
        TokenSet(access_token="at-value", expires_in=3599, refresh_token=refresh_token)
    )

    assert response.status_code == 303
    assert response.headers["location"] == "http://localhost:8000/docs"

    cookies = _set_cookies(response)
    access = cookies["access_token"]
    assert access.startswith("access_token=at-value;")
    assert "HttpOnly" in access
    assert "Secure" in access
    assert "SameSite=strict" in access
    assert "Path=/;" in access or access.endswith("Path=/")
    assert "Max-Age=3599" in access

    refresh = cookies["refresh_token"]
    assert refresh.startswith(f"refresh_token={refresh_token};")
    assert "HttpOnly" in refresh
    assert "Secure" in refresh
    assert "SameSite=strict" in refresh
    assert "Path=/refresh" in refresh
    max_age = int(refresh.split("Max-Age=")[1].split(";")[0])
    assert 30 * 24 * 3600 - 5 <= max_age <= 30 * 24 * 3600

    assert response.headers["cache-control"] == "no-cache, no-store, must-revalidate"
    assert response.headers["pragma"] == "no-cache"
    assert response.headers["expires"] == "0"


def test_session_cookies_without_refresh_token_set_access_cookie_only():
    handler, _ = _handler_with_mock_rph()
    response = Response()

    handler.set_session_cookies(
        response, TokenSet(access_token="at", expires_in=60, refresh_token=None)
    )

    assert set(_set_cookies(response)) == {"access_token"}


@pytest.mark.parametrize(
    ("tokens", "cookie"),
    [
        (TokenSet("at", None, None), "access_token"),
        (TokenSet("at", 60, "opaque-refresh-token"), "refresh_token"),
        (TokenSet("at", 60, _refresh_token(exp_in=None)), "refresh_token"),
    ],
    ids=["no-expires_in", "opaque-refresh", "refresh-without-exp"],
)
def test_cookie_without_known_lifetime_lasts_the_browser_session(tokens, cookie):
    handler, _ = _handler_with_mock_rph()
    response = Response()

    handler.set_session_cookies(response, tokens)

    assert "Max-Age" not in _set_cookies(response)[cookie]


def test_expired_refresh_token_cookie_expires_at_once():
    handler, _ = _handler_with_mock_rph()
    response = Response()

    handler.set_session_cookies(
        response, TokenSet("at", 60, _refresh_token(exp_in=-60))
    )

    assert "Max-Age=0" in _set_cookies(response)["refresh_token"]


def test_initiate_web_session_omits_secure_when_configured_off(monkeypatch):
    monkeypatch.setenv("OIDC_SECURE_COOKIE", "false")
    handler, _ = _handler_with_mock_rph()

    response = handler.initiate_web_session(
        TokenSet(access_token="at", expires_in=60, refresh_token=_refresh_token())
    )

    for cookie in _set_cookies(response).values():
        assert "Secure" not in cookie


def test_logout_clears_both_cookies_and_redirects():
    handler, _ = _handler_with_mock_rph()

    response = handler.logout()

    assert response.status_code == 303
    assert response.headers["location"] == "http://localhost:8000/"

    cookies = _set_cookies(response)
    assert "Max-Age=0" in cookies["access_token"]
    assert "Path=/;" in cookies["access_token"]
    assert "Max-Age=0" in cookies["refresh_token"]
    assert "Path=/refresh" in cookies["refresh_token"]
    for cookie in cookies.values():
        assert "HttpOnly" in cookie
        assert "Secure" in cookie
        assert "SameSite=strict" in cookie
