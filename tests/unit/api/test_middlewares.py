"""Unit tests for search_api.api.middlewares."""

import os
from base64 import b64encode

os.environ["JWT_KEY"] = b64encode(
    b"test-jwt-signing-key-at-least-32-bytes-long"
).decode("ascii")
os.environ["JWT_ISSUER"] = "sd-search-api-test"
os.environ["JWT_ALGORITHM"] = "HS256"

import pytest

from search_api.api.middlewares import AuthMiddleware
from search_api.services.session import create_jwt_token


PROTECTED_PATH = "/protected"


def _make_scope(
    path: str, headers: list[tuple[bytes, bytes]] | None = None, method: str = "GET"
) -> dict:
    return {
        "type": "http",
        "method": method,
        "path": path,
        "headers": headers or [],
    }


async def _receive() -> dict:
    return {"type": "http.request", "body": b"", "more_body": False}


def _make_downstream_app():
    called = {"value": False}

    async def app(scope, receive, send):
        called["value"] = True
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok", "more_body": False})

    return app, called


def _cookie_header(token: str) -> list[tuple[bytes, bytes]]:
    return [(b"cookie", f"access_token={token}".encode())]


def _bearer_header(token: str) -> list[tuple[bytes, bytes]]:
    return [(b"authorization", f"Bearer {token}".encode())]


@pytest.mark.asyncio
async def test_public_path_passes_through_without_token():
    app, called = _make_downstream_app()
    middleware = AuthMiddleware(app)

    scope = _make_scope("/health")
    sent = []

    async def send(message):
        sent.append(message)

    await middleware(scope, _receive, send)

    assert called["value"]
    assert sent[0]["status"] == 200


@pytest.mark.asyncio
async def test_protected_path_without_token_returns_401_and_skips_downstream():
    app, called = _make_downstream_app()
    middleware = AuthMiddleware(app)

    scope = _make_scope(PROTECTED_PATH, method="POST")
    sent = []

    async def send(message):
        sent.append(message)

    await middleware(scope, _receive, send)

    assert not called["value"]
    assert sent[0]["type"] == "http.response.start"
    assert sent[0]["status"] == 401
    body = next(m["body"] for m in sent if m["type"] == "http.response.body")
    assert body == b'{"detail":"Not authenticated."}'


@pytest.mark.asyncio
async def test_protected_path_with_valid_cookie_succeeds():
    app, called = _make_downstream_app()
    middleware = AuthMiddleware(app)

    token = create_jwt_token("user-1", "Jane Doe")
    scope = _make_scope(PROTECTED_PATH, _cookie_header(token), method="POST")
    sent = []

    async def send(message):
        sent.append(message)

    await middleware(scope, _receive, send)

    assert called["value"]
    assert sent[0]["status"] == 200
    assert scope["state"]["user_id"] == "user-1"


@pytest.mark.asyncio
async def test_protected_path_with_valid_bearer_header_succeeds():
    app, called = _make_downstream_app()
    middleware = AuthMiddleware(app)

    token = create_jwt_token("user-1", "Jane Doe")
    scope = _make_scope(PROTECTED_PATH, _bearer_header(token), method="POST")
    sent = []

    async def send(message):
        sent.append(message)

    await middleware(scope, _receive, send)

    assert called["value"]
    assert sent[0]["status"] == 200
    assert scope["state"]["user_id"] == "user-1"


@pytest.mark.asyncio
async def test_protected_path_with_expired_token_returns_401():
    from datetime import timedelta

    app, called = _make_downstream_app()
    middleware = AuthMiddleware(app)

    token = create_jwt_token("user-1", "Jane Doe", expiration=timedelta(seconds=-1))
    scope = _make_scope(PROTECTED_PATH, _cookie_header(token), method="POST")
    sent = []

    async def send(message):
        sent.append(message)

    await middleware(scope, _receive, send)

    assert not called["value"]
    assert sent[0]["status"] == 401


@pytest.mark.asyncio
async def test_protected_path_with_tampered_token_returns_401():
    app, called = _make_downstream_app()
    middleware = AuthMiddleware(app)

    token = create_jwt_token("user-1", "Jane Doe")
    # Flip a character in the middle of the signature. The
    # final base64url character of a 32-byte HMAC-SHA256 signature only
    # carries 4 significant bits (2 are unused padding), so a swap
    # there can decode to the identical signature bytes.
    middle = len(token) // 2
    flipped_char = "A" if token[middle] != "A" else "B"
    tampered = token[:middle] + flipped_char + token[middle + 1 :]
    scope = _make_scope(PROTECTED_PATH, _cookie_header(tampered), method="POST")
    sent = []

    async def send(message):
        sent.append(message)

    await middleware(scope, _receive, send)

    assert not called["value"]
    assert sent[0]["status"] == 401


@pytest.mark.asyncio
async def test_admin_path_passes_through_unauthenticated():
    app, called = _make_downstream_app()
    middleware = AuthMiddleware(app)

    scope = _make_scope("/admin/snomed/refresh")
    sent = []

    async def send(message):
        sent.append(message)

    await middleware(scope, _receive, send)

    assert called["value"]
    assert sent[0]["status"] == 200


@pytest.mark.asyncio
async def test_protected_path_with_non_jwt_validation_error_returns_401(monkeypatch):
    import search_api.api.middlewares as middlewares_module

    def _raise(_token):
        raise ValueError("JWT_KEY misconfigured")

    monkeypatch.setattr(middlewares_module, "validate_jwt_token", _raise)

    app, called = _make_downstream_app()
    middleware = AuthMiddleware(app)

    scope = _make_scope(
        PROTECTED_PATH, _cookie_header("irrelevant-token"), method="POST"
    )
    sent = []

    async def send(message):
        sent.append(message)

    await middleware(scope, _receive, send)

    assert not called["value"]
    assert sent[0]["status"] == 401
