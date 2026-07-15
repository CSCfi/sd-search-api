"""Unit tests for search_api.services.auth_service.AuthServiceHandler."""

import os
from base64 import b64encode
from unittest.mock import MagicMock

os.environ["BASE_URL"] = "http://localhost:8000"
os.environ["OIDC_URL"] = "http://localhost:9999"
os.environ["OIDC_CLIENT_ID"] = "test-client-id"
os.environ["OIDC_CLIENT_SECRET"] = "test-client-secret"
os.environ["JWT_KEY"] = b64encode(
    b"test-jwt-signing-key-at-least-32-bytes-long"
).decode("ascii")
os.environ["JWT_ISSUER"] = "sd-search-api-test"
os.environ["JWT_ALGORITHM"] = "HS256"

import pytest
from fastapi import HTTPException
from idpyoidc.client.exception import OidcServiceError
from requests.exceptions import ConnectionError as RequestsConnectionError

from search_api.exceptions import SystemException
from search_api.services.auth_service import AuthServiceHandler
from search_api.services.session import validate_jwt_token


def _handler_with_mock_rph() -> tuple[AuthServiceHandler, MagicMock]:
    handler = AuthServiceHandler()
    mock_rph = MagicMock()
    handler._rph = mock_rph
    return handler, mock_rph


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


@pytest.mark.asyncio
async def test_callback_unknown_state_raises_401():
    handler, mock_rph = _handler_with_mock_rph()
    mock_rph.get_session_information.side_effect = KeyError("unknown-state")

    with pytest.raises(HTTPException) as exc_info:
        await handler.callback("unknown-state", "some-code")

    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_callback_finalize_key_error_raises_401():
    handler, mock_rph = _handler_with_mock_rph()
    mock_rph.get_session_information.return_value = {"iss": "http://localhost:9999"}
    mock_rph.finalize.side_effect = KeyError("http://localhost:9999")

    with pytest.raises(HTTPException) as exc_info:
        await handler.callback("known-state", "some-code")

    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_callback_finalize_protocol_error_raises_401():
    handler, mock_rph = _handler_with_mock_rph()
    mock_rph.get_session_information.return_value = {"iss": "http://localhost:9999"}
    mock_rph.finalize.side_effect = OidcServiceError("invalid_grant")

    with pytest.raises(HTTPException) as exc_info:
        await handler.callback("known-state", "rejected-code")

    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_callback_finalize_connection_error_raises_system_exception():
    handler, mock_rph = _handler_with_mock_rph()
    mock_rph.get_session_information.return_value = {"iss": "http://localhost:9999"}
    mock_rph.finalize.side_effect = RequestsConnectionError(
        "token endpoint unreachable"
    )

    with pytest.raises(SystemException):
        await handler.callback("known-state", "some-code")


@pytest.mark.asyncio
async def test_callback_missing_access_token_raises_401():
    handler, mock_rph = _handler_with_mock_rph()
    mock_rph.get_session_information.return_value = {"iss": "http://localhost:9999"}
    mock_rph.finalize.side_effect = KeyError("no access token")

    with pytest.raises(HTTPException) as exc_info:
        await handler.callback("known-state", "some-code")

    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_callback_success_returns_jwt():
    handler, mock_rph = _handler_with_mock_rph()
    session_info = {"iss": "http://localhost:9999"}
    mock_rph.get_session_information.return_value = session_info
    mock_rph.finalize.return_value = {
        "userinfo": {"sub": "user-123", "given_name": "Jane", "family_name": "Doe"}
    }

    jwt_token = await handler.callback("known-state", "good-code")

    user_id, user_name = validate_jwt_token(jwt_token)
    assert user_id == "user-123"
    assert user_name == "Jane Doe"

    # `code` is merged into the session info fetched from `get_session_information`
    # before being handed to `finalize`, alongside the configured issuer.
    mock_rph.finalize.assert_called_once_with(
        "http://localhost:9999", {"iss": "http://localhost:9999", "code": "good-code"}
    )


def test_initiate_web_session_sets_cookie_and_redirects():
    handler, _ = _handler_with_mock_rph()

    response = handler.initiate_web_session("jwt-token-value")

    assert response.status_code == 303
    assert response.headers["location"] == "http://localhost:8000/docs"

    set_cookie = response.headers["set-cookie"]
    assert "access_token=jwt-token-value" in set_cookie
    assert "HttpOnly" in set_cookie
    assert "Secure" in set_cookie
    assert "SameSite=strict" in set_cookie
    assert "Path=/" in set_cookie
    assert "Max-Age=604800" in set_cookie  # JWT_EXPIRATION == 7 days

    assert response.headers["cache-control"] == "no-cache, no-store, must-revalidate"
    assert response.headers["pragma"] == "no-cache"
    assert response.headers["expires"] == "0"


def test_initiate_web_session_omits_secure_when_configured_off(monkeypatch):
    monkeypatch.setenv("OIDC_SECURE_COOKIE", "false")
    handler, _ = _handler_with_mock_rph()

    response = handler.initiate_web_session("jwt-token-value")

    assert "Secure" not in response.headers["set-cookie"]


def test_logout_clears_cookie_and_redirects():
    handler, _ = _handler_with_mock_rph()

    response = handler.logout()

    assert response.status_code == 303
    assert response.headers["location"] == "http://localhost:8000/docs"

    set_cookie = response.headers["set-cookie"]
    assert set_cookie.startswith("access_token=")
    assert "Max-Age=0" in set_cookie
    assert "Path=/" in set_cookie
    assert "HttpOnly" in set_cookie
    assert "Secure" in set_cookie
    assert "SameSite=strict" in set_cookie
