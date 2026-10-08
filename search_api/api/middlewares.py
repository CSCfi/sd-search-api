"""Request authentication middleware."""

import logging
from http.cookies import SimpleCookie

from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from search_api.exceptions import SystemException
from search_api.services.access_token import AccessTokenValidator, InvalidAccessToken

logger = logging.getLogger(__name__)

AUTH_COOKIE = "access_token"

PUBLIC_PATHS = frozenset(
    {
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
    }
)


def _is_public_path(path: str) -> bool:
    return path in PUBLIC_PATHS or path == "/admin" or path.startswith("/admin/")


def _extract_token(headers: Headers) -> str | None:
    """Extract the access token from the request headers.

    The token may be in a cookie or in the Authorization header.
    """
    cookie_header = headers.get("cookie")
    if cookie_header:
        cookies = SimpleCookie()
        cookies.load(cookie_header)
        if AUTH_COOKIE in cookies:
            return cookies[AUTH_COOKIE].value

    auth_header = headers.get("authorization")
    if auth_header:
        parts = auth_header.split()
        if len(parts) == 2 and parts[0].lower() == "bearer":
            return parts[1]

    return None


class AuthMiddleware:
    """Enforce a valid access token on every request except an explicit public allow-list."""

    def __init__(self, app: ASGIApp, validator: AccessTokenValidator) -> None:
        self.app = app
        self.validator = validator

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or _is_public_path(scope["path"]):
            await self.app(scope, receive, send)
            return

        token = _extract_token(Headers(scope=scope))
        if token is None:
            await _send_unauthorized(scope, receive, send)
            return

        # Middleware added by `add_middleware` sits outside the exception handlers, so
        # it answers both failures itself rather than raising.
        try:
            user_id = await self.validator.validate(token)
        except InvalidAccessToken:
            await _send_unauthorized(scope, receive, send)
            return
        except SystemException:
            # The issuer is needed and unreachable: a 401 would send the client round
            # /refresh and /login, which fail the same way.
            logger.exception("Access token validation failed.")
            await _send_unavailable(scope, receive, send)
            return

        scope.setdefault("state", {})["user_id"] = user_id
        await self.app(scope, receive, send)


async def _send_unauthorized(scope: Scope, receive: Receive, send: Send) -> None:
    response = JSONResponse(status_code=401, content={"detail": "Not authenticated."})
    await response(scope, receive, send)


async def _send_unavailable(scope: Scope, receive: Receive, send: Send) -> None:
    response = JSONResponse(status_code=503, content={"detail": "Service error."})
    await response(scope, receive, send)
