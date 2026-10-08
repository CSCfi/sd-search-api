"""OIDC authentication routes."""

from urllib.parse import parse_qs, urlparse

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response

from search_api.conf import oidc_config
from search_api.services.auth import REFRESH_COOKIE, AuthServiceHandler, RefreshRejected

# Binds the login attempt to the browser that started it: /login sets this to the
# OIDC `state` value and /callback requires it to match the `state` query param.
# Without it, a `state`+`code` pair intercepted or shared by an attacker (e.g. via
# their own proxy) could be replayed by a victim's browser and get them signed in as
# the attacker (login CSRF / session fixation). SameSite=Lax (not Strict, unlike the
# session cookie) because it must survive the top-level cross-site redirect back from
# the IdP to /callback.
OIDC_STATE_COOKIE = "oidc_state"
OIDC_STATE_COOKIE_MAX_AGE = 600


# Dependency provider is module-level so tests can override it by identity via
# app.dependency_overrides. It resolves the shared service from app.state, which
# a later wiring step populates.
def get_auth_service(request: Request) -> AuthServiceHandler:
    return request.app.state.auth_service


router = APIRouter()


@router.get("/login")
async def login(
    auth_service: AuthServiceHandler = Depends(get_auth_service),
) -> RedirectResponse:
    auth_url = await auth_service.get_oidc_auth_url()
    state = parse_qs(urlparse(auth_url).query).get("state", [""])[0]

    response = RedirectResponse(url=auth_url, status_code=303)
    response.set_cookie(
        key=OIDC_STATE_COOKIE,
        value=state,
        httponly=True,
        secure=oidc_config().OIDC_SECURE_COOKIE,
        samesite="lax",
        path="/callback",
        max_age=OIDC_STATE_COOKIE_MAX_AGE,
    )
    return response


@router.get("/callback", include_in_schema=False)
async def callback(
    state: str,
    code: str,
    request: Request,
    auth_service: AuthServiceHandler = Depends(get_auth_service),
) -> RedirectResponse:
    if not state or request.cookies.get(OIDC_STATE_COOKIE) != state:
        raise HTTPException(
            status_code=401, detail="Login session state mismatch or expired."
        )

    tokens = await auth_service.callback(state, code)
    response = auth_service.initiate_web_session(tokens)
    response.delete_cookie(
        OIDC_STATE_COOKIE,
        path="/callback",
        secure=oidc_config().OIDC_SECURE_COOKIE,
        httponly=True,
        samesite="lax",
    )
    return response


@router.post("/refresh", status_code=204, response_class=Response)
async def refresh(
    request: Request,
    auth_service: AuthServiceHandler = Depends(get_auth_service),
) -> Response:
    """Renew the session from the refresh cookie, replacing both session cookies.

    `401` means the session is over: log in again. `503` means the identity provider
    could not be reached; the cookies are left as they were, so a later retry can work.
    """
    refresh_token = request.cookies.get(REFRESH_COOKIE)
    if not refresh_token:
        raise HTTPException(status_code=401, detail="No refresh token.")

    try:
        tokens = await auth_service.refresh(refresh_token)
    except RefreshRejected:
        rejected = JSONResponse(
            status_code=401, content={"detail": "Refresh token rejected."}
        )
        auth_service.clear_session_cookies(rejected)
        return rejected

    response = Response(status_code=204)
    auth_service.set_session_cookies(response, tokens)
    return response


@router.get("/logout")
async def logout(
    auth_service: AuthServiceHandler = Depends(get_auth_service),
) -> RedirectResponse:
    return auth_service.logout()
