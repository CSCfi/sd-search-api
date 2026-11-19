"""Request authentication middleware."""

import logging

from starlette.datastructures import Headers, MutableHeaders
from starlette.requests import cookie_parser
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from search_api.exceptions import SystemException
from search_api.services.access_token import AccessTokenValidator, InvalidAccessToken
from search_api.services.auth import (
    AuthServiceHandler,
    RefreshRejected,
    TokenSet,
    refresh_token_from_cookies,
)

logger = logging.getLogger(__name__)

AUTH_COOKIE = "access_token"

# The headers besides `Set-Cookie` that setting the session cookies writes, replacing
# the response's own: a response carrying tokens must not be cached.
_SESSION_HEADERS = frozenset({"cache-control", "pragma", "expires"})

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
        token = cookie_parser(cookie_header).get(AUTH_COOKIE)
        if token:
            return token

    auth_header = headers.get("authorization")
    if auth_header:
        parts = auth_header.split()
        if len(parts) == 2 and parts[0].lower() == "bearer":
            return parts[1]

    return None


class AuthMiddleware:
    """Enforce a valid access token on every request except an explicit public allow-list."""

    def __init__(
        self,
        app: ASGIApp,
        validator: AccessTokenValidator,
        auth_service: AuthServiceHandler,
    ) -> None:
        self.app = app
        self.validator = validator
        self.auth_service = auth_service

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or _is_public_path(scope["path"]):
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        token = _extract_token(headers)

        # Middleware added by `add_middleware` sits outside the exception handlers, so
        # it answers both failures itself rather than raising.
        if token is not None:
            try:
                user_id = await self.validator.validate(token)
            except InvalidAccessToken:
                pass  # renewed below, given a refresh cookie
            except SystemException:
                # The issuer is needed and unreachable: a 401 would send the client round
                # /refresh and /login, which fail the same way. A renewed token could
                # not be validated either.
                logger.exception("Access token validation failed.")
                await _send_unavailable(scope, receive, send)
                return
            else:
                scope.setdefault("state", {})["user_id"] = user_id
                await self.app(scope, receive, send)
                return

        refresh_token = refresh_token_from_cookies(headers.get("cookie"))
        if refresh_token is None:
            await _send_unauthorized(scope, receive, send)
            return

        try:
            tokens = await self.auth_service.refresh_shared(refresh_token)
            user_id = await self.validator.validate(tokens.access_token)
        except RefreshRejected, InvalidAccessToken:
            # The cookies are left alone, as `/refresh` leaves them: another tab may
            # just have been given new ones.
            await _send_unauthorized(scope, receive, send)
            return
        except SystemException:
            logger.exception("Session refresh failed.")
            await _send_unavailable(scope, receive, send)
            return

        scope.setdefault("state", {})["user_id"] = user_id
        await self.app(scope, receive, self._setting_session_cookies(send, tokens))

    def _setting_session_cookies(self, send: Send, tokens: TokenSet) -> Send:
        """`send`, adding the renewed session's cookies to the response.

        Whatever its status: the refresh token the request came with is spent. The
        cookies are rendered by the very code `/refresh` sets them with.
        """
        rendered = Response()
        self.auth_service.set_session_cookies(rendered, tokens)
        session_headers = [
            (name.decode("latin-1"), value.decode("latin-1"))
            for name, value in rendered.raw_headers
            if name == b"set-cookie" or name.decode("latin-1") in _SESSION_HEADERS
        ]

        async def send_with_cookies(message: Message) -> None:
            if message["type"] == "http.response.start":
                message.setdefault("headers", [])
                response_headers = MutableHeaders(scope=message)
                for name, value in session_headers:
                    if name == "set-cookie":
                        response_headers.append(name, value)
                    else:
                        response_headers[name] = value
            await send(message)

        return send_with_cookies


async def _send_unauthorized(scope: Scope, receive: Receive, send: Send) -> None:
    response = JSONResponse(status_code=401, content={"detail": "Not authenticated."})
    await response(scope, receive, send)


async def _send_unavailable(scope: Scope, receive: Receive, send: Send) -> None:
    response = JSONResponse(status_code=503, content={"detail": "Service error."})
    await response(scope, receive, send)
