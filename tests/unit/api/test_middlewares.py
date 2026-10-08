"""Unit tests for search_api.api.middlewares."""

import pytest

from search_api.api.middlewares import AuthMiddleware
from search_api.exceptions import SystemException
from search_api.services.access_token import InvalidAccessToken, SigningKeysUnavailable

PROTECTED_PATH = "/protected"
VALID_TOKEN = "valid-access-token"


class FakeValidator:
    """Accepts `VALID_TOKEN` as user-1, rejects anything else, or raises `error`."""

    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.seen: list[str] = []

    async def validate(self, token: str) -> str:
        self.seen.append(token)
        if self.error:
            raise self.error
        if token != VALID_TOKEN:
            raise InvalidAccessToken("rejected")
        return "user-1"


async def _run(
    path: str,
    headers: list[tuple[bytes, bytes]] | None = None,
    validator: FakeValidator | None = None,
) -> tuple[int, bytes, dict, bool]:
    """Send one request through the middleware: status, body, scope, downstream ran."""
    called = {"value": False}

    async def app(scope, receive, send):
        called["value"] = True
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok", "more_body": False})

    async def receive() -> dict:
        return {"type": "http.request", "body": b"", "more_body": False}

    sent: list[dict] = []

    async def send(message):
        sent.append(message)

    scope = {"type": "http", "method": "POST", "path": path, "headers": headers or []}
    await AuthMiddleware(app, validator or FakeValidator())(scope, receive, send)

    status = next(m["status"] for m in sent if m["type"] == "http.response.start")
    body = b"".join(m["body"] for m in sent if m["type"] == "http.response.body")
    return status, body, scope, called["value"]


def _cookie(token: str, others: str = "") -> list[tuple[bytes, bytes]]:
    return [(b"cookie", f"{others}access_token={token}".encode())]


def _authorization(value: str) -> list[tuple[bytes, bytes]]:
    return [(b"authorization", value.encode())]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path",
    [
        "/health",
        "/info",
        "/jwk",
        "/login",
        "/callback",
        "/refresh",
        "/logout",
        "/docs",
        "/openapi.json",
        "/redoc",
        "/",
        "/admin",
        "/admin/snomed/refresh",
    ],
)
async def test_public_paths_pass_through_without_token(path):
    validator = FakeValidator()

    status, _, _, called = await _run(path, validator=validator)

    assert called
    assert status == 200
    assert validator.seen == []


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/adminx", "/refreshx", "/login/extra", "/info/x"])
async def test_lookalikes_of_public_paths_are_protected(path):
    status, _, _, called = await _run(path)

    assert not called
    assert status == 401


@pytest.mark.asyncio
async def test_protected_path_without_token_returns_401_and_skips_downstream():
    status, body, _, called = await _run(PROTECTED_PATH)

    assert not called
    assert status == 401
    assert body == b'{"detail":"Not authenticated."}'


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "headers",
    [
        _cookie(VALID_TOKEN),
        _cookie(VALID_TOKEN, others="theme=dark; "),
        _authorization(f"Bearer {VALID_TOKEN}"),
        _authorization(f"bearer {VALID_TOKEN}"),
    ],
    ids=["cookie", "cookie-among-others", "bearer", "bearer-lowercase"],
)
async def test_valid_token_authenticates_as_its_subject(headers):
    status, _, scope, called = await _run(PROTECTED_PATH, headers)

    assert called
    assert status == 200
    assert scope["state"]["user_id"] == "user-1"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "value", [f"Basic {VALID_TOKEN}", "Bearer", f"Bearer {VALID_TOKEN} extra", ""]
)
async def test_malformed_authorization_header_is_not_a_token(value):
    validator = FakeValidator()

    status, _, _, called = await _run(PROTECTED_PATH, _authorization(value), validator)

    assert not called
    assert status == 401
    assert validator.seen == []


@pytest.mark.asyncio
async def test_rejected_token_returns_401():
    validator = FakeValidator()

    status, _, _, called = await _run(
        PROTECTED_PATH, _cookie("expired-or-forged"), validator
    )

    assert not called
    assert status == 401
    assert validator.seen == ["expired-or-forged"]


@pytest.mark.asyncio
async def test_cookie_takes_precedence_over_bearer_header():
    validator = FakeValidator()
    headers = _cookie(VALID_TOKEN) + _authorization("Bearer other-token")

    status, _, _, _ = await _run(PROTECTED_PATH, headers, validator)

    assert status == 200
    assert validator.seen == [VALID_TOKEN]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [
        SigningKeysUnavailable("OIDC signing keys unreachable."),
        SystemException("OIDC discovery failed."),
    ],
)
async def test_issuer_unreachable_returns_503(error):
    status, body, _, called = await _run(
        PROTECTED_PATH, _cookie(VALID_TOKEN), FakeValidator(error)
    )

    assert not called
    assert status == 503
    assert body == b'{"detail":"Service error."}'


@pytest.mark.asyncio
async def test_unexpected_validation_error_is_not_turned_into_401():
    # A bug must surface, not send the client round /refresh and /login.
    with pytest.raises(RuntimeError):
        await _run(
            PROTECTED_PATH, _cookie(VALID_TOKEN), FakeValidator(RuntimeError("bug"))
        )


@pytest.mark.asyncio
async def test_non_http_scope_passes_through():
    called = {"value": False}

    async def app(scope, receive, send):
        called["value"] = True

    validator = FakeValidator()
    await AuthMiddleware(app, validator)({"type": "lifespan"}, None, None)  # type: ignore[arg-type]

    assert called["value"]
    assert validator.seen == []
