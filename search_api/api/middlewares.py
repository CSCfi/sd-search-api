"""Request authentication middleware."""

from http.cookies import SimpleCookie

from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from search_api.services.session import validate_jwt_token

AUTH_COOKIE = "access_token"

PUBLIC_PATHS = frozenset(
    {
        "/health",
        "/info",
        "/login",
        "/callback",
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
    """Extract a session token from the request headers.

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
    """Enforce a valid session on every request except an explicit public allow-list."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or _is_public_path(scope["path"]):
            await self.app(scope, receive, send)
            return

        token = _extract_token(Headers(scope=scope))
        if token is None:
            await _send_unauthorized(scope, receive, send)
            return

        try:
            user_id, _ = validate_jwt_token(token)
        except Exception:
            # Anything that goes wrong validating token (a malformed/expired/tampered
            # JWT, or a config error reading JWT_KEY/JWT_ISSUER) must become a 401 here
            await _send_unauthorized(scope, receive, send)
            return

        scope.setdefault("state", {})["user_id"] = user_id
        await self.app(scope, receive, send)


async def _send_unauthorized(scope: Scope, receive: Receive, send: Send) -> None:
    response = JSONResponse(status_code=401, content={"detail": "Not authenticated."})
    await response(scope, receive, send)
