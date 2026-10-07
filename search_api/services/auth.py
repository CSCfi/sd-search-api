"""OIDC relying party service."""

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any

import httpx
import jwt
from cryptojwt import KeyJar  # type: ignore[import-untyped]
from fastapi import HTTPException
from idpyoidc.client.exception import OidcServiceError  # type: ignore[import-untyped]
from idpyoidc.client.rp_handler import RPHandler  # type: ignore[import-untyped]
from idpyoidc.exception import OidcMsgError  # type: ignore[import-untyped]
from requests.exceptions import RequestException
from starlette.responses import RedirectResponse, Response

from search_api.conf import oidc_config
from search_api.exceptions import SystemException
from search_api.services.oidc_metadata import ProviderMetadata

SESSION_COOKIE = "access_token"
# Scoped to the one path that redeems it, so the long-lived token rides on no other
# request.
REFRESH_COOKIE = "refresh_token"
REFRESH_COOKIE_PATH = "/refresh"

REFRESH_TIMEOUT = 10.0

# idpyoidc's RPHandler logs the client config -- including OIDC_CLIENT_SECRET -- at
# DEBUG on init. Capped here, independent of whatever level the app's own root logger
# ends up at, so raising verbosity elsewhere can never leak the secret.
logging.getLogger("idpyoidc").setLevel(logging.INFO)


@dataclass(frozen=True)
class TokenSet:
    """The identity provider's tokens that make up a session."""

    access_token: str
    expires_in: int | None
    refresh_token: str | None


class RefreshRejected(Exception):
    """The identity provider refused the refresh token: the session is over."""


class AuthServiceHandler:
    """OIDC Authorization Code + PKCE relying party, backed by idpyoidc's `RPHandler`."""

    def __init__(self, metadata: ProviderMetadata | None = None) -> None:
        self._metadata = metadata or ProviderMetadata()
        self._rph: RPHandler | None = None
        # RPHandler's calls are synchronous (built on `requests`) and get dispatched to a
        # thread via asyncio.to_thread; the lock keeps concurrent logins from racing on the
        # shared RPHandler's internal session/state dict once that introduces real threading.
        self._rph_lock = asyncio.Lock()

    @property
    def rph(self) -> RPHandler:
        if self._rph is None:
            config = oidc_config()
            self._rph = RPHandler(
                config.OIDC_URL.rstrip("/") + "/.well-known/openid-configuration",
                client_configs=self.get_client_configs(),
                # Empty KeyJar prevents RPHandler from generating and persisting an unused
                # keypair to ./private/jwks.json. IdP keys are fetched during discovery.
                keyjar=KeyJar(),
            )
        return self._rph

    def get_client_configs(self) -> dict[str, dict[str, Any]]:
        """Return the idpyoidc client configuration keyed by provider alias."""
        config = oidc_config()
        return {
            "aai": {
                "issuer": config.OIDC_URL,
                "client_id": config.OIDC_CLIENT_ID,
                "client_secret": config.OIDC_CLIENT_SECRET,
                "client_type": "oidc",
                "client_authn_methods": ["client_secret_basic"],
                "redirect_uris": [config.callback_url],
                "preference": {
                    "response_types_supported": ["code"],
                    "scopes_supported": config.OIDC_SCOPE.split(" "),
                },
                "add_ons": {
                    "pkce": {
                        "function": "idpyoidc.client.oauth2.add_on.pkce.add_support",
                        "kwargs": {
                            "code_challenge_length": 64,
                            "code_challenge_method": "S256",
                        },
                    },
                },
            },
        }

    async def get_oidc_auth_url(self) -> str:
        """Start a new OIDC Authorization Code + PKCE flow and return the IdP authorization URL."""
        async with self._rph_lock:
            try:
                authorization_url = await asyncio.to_thread(self.rph.begin, "aai")
            except Exception as exc:
                raise SystemException("OIDC issuer unreachable.") from exc

        return str(authorization_url)

    async def callback(self, state: str, code: str) -> TokenSet:
        """Exchange the authorization code and return the identity provider's tokens."""
        async with self._rph_lock:
            try:
                session_info = await asyncio.to_thread(
                    self.rph.get_session_information, state
                )
            except KeyError as exc:
                raise HTTPException(
                    status_code=401, detail="Unknown or expired login session."
                ) from exc

            session_info["code"] = code

            try:
                session = await asyncio.to_thread(
                    self.rph.finalize, oidc_config().OIDC_URL, session_info
                )
            except KeyError as exc:
                # RPHandler.finalize looks up the issuer in its own client registry
                # internally; a mismatch (e.g. configured OIDC_URL doesn't match the
                # IdP's reported issuer) raises KeyError, not a parsed protocol error.
                raise HTTPException(
                    status_code=401,
                    detail="OIDC issuer not recognized for this login session.",
                ) from exc
            except (OidcMsgError, OidcServiceError) as exc:
                # idpyoidc's own parsed protocol-error types: the IdP rejected the code
                # itself (invalid/expired/already used) or the token/ID-token response
                # otherwise failed validation. Protocol-validity failure -> 401.
                raise HTTPException(
                    status_code=401,
                    detail="OIDC provider rejected the authorization code.",
                ) from exc
            except RequestException as exc:
                # Connection/timeout-level failure talking to the IdP -> dependency
                # failure, not a bad credential -> 503.
                raise SystemException("OIDC token exchange failed.") from exc

            if "error" in session:
                raise HTTPException(
                    status_code=401, detail="OIDC provider returned an error."
                )

            # finalize returns only the access token; the rest of the token response
            # is kept in the client's per-login state.
            client = self.rph.get_client_from_session_key(state)
            token_response = client.get_context().cstate.get_set(
                state, claim=["access_token", "expires_in", "refresh_token"]
            )
            # The login is complete: drop its state rather than keep it for the life
            # of the process.
            self.rph.clear_session(state)

        access_token = token_response.get("access_token")
        if not access_token:
            raise HTTPException(
                status_code=401, detail="OIDC provider issued no access token."
            )
        expires_in = token_response.get("expires_in")
        return TokenSet(
            access_token=access_token,
            expires_in=int(expires_in) if expires_in is not None else None,
            refresh_token=token_response.get("refresh_token"),
        )

    async def refresh(self, refresh_token: str) -> TokenSet:
        """Redeem a refresh token at the token endpoint.

        Posts to the token endpoint itself rather than through `RPHandler`, which finds
        a session by its login `state` in process memory: that would fail after a
        restart, on any other replica, and once the callback has cleared it.
        """
        token_endpoint = (await self._metadata.get())["token_endpoint"]
        config = oidc_config()
        try:
            async with httpx.AsyncClient(timeout=REFRESH_TIMEOUT) as client:
                response = await client.post(
                    token_endpoint,
                    data={
                        "grant_type": "refresh_token",
                        "refresh_token": refresh_token,
                    },
                    # client_secret_basic, as idpyoidc sends it for the code exchange.
                    auth=(config.OIDC_CLIENT_ID, config.OIDC_CLIENT_SECRET),
                )
        except httpx.HTTPError as exc:
            raise SystemException("OIDC token refresh failed.") from exc

        if response.status_code in (400, 401):
            # invalid_grant: expired, revoked, or spent by an earlier refresh.
            raise RefreshRejected(response.text)
        if not response.is_success:
            raise SystemException(f"OIDC token refresh failed: {response.status_code}.")

        try:
            body = response.json()
            access_token = body["access_token"]
        except (ValueError, KeyError) as exc:
            raise SystemException(
                "OIDC token refresh returned no access token."
            ) from exc

        expires_in = body.get("expires_in")
        return TokenSet(
            access_token=access_token,
            expires_in=int(expires_in) if expires_in is not None else None,
            # Kept when the provider does not rotate it.
            refresh_token=body.get("refresh_token") or refresh_token,
        )

    def initiate_web_session(self, tokens: TokenSet) -> RedirectResponse:
        """Set the session cookies and redirect to the post-login URL."""
        response = RedirectResponse(url=oidc_config().redirect_url, status_code=303)
        self.set_session_cookies(response, tokens)
        return response

    def set_session_cookies(self, response: Response, tokens: TokenSet) -> None:
        """Set the access token and, when there is one, the refresh token cookie."""
        secure = oidc_config().OIDC_SECURE_COOKIE
        response.set_cookie(
            key=SESSION_COOKIE,
            value=tokens.access_token,
            httponly=True,
            secure=secure,
            samesite="strict",
            path="/",
            max_age=tokens.expires_in,
        )
        if tokens.refresh_token:
            response.set_cookie(
                key=REFRESH_COOKIE,
                value=tokens.refresh_token,
                httponly=True,
                secure=secure,
                samesite="strict",
                path=REFRESH_COOKIE_PATH,
                max_age=_seconds_until_expiry(tokens.refresh_token),
            )
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"

    def clear_session_cookies(self, response: Response) -> None:
        """Delete both session cookies, each on the path it was set with."""
        secure = oidc_config().OIDC_SECURE_COOKIE
        for key, path in ((SESSION_COOKIE, "/"), (REFRESH_COOKIE, REFRESH_COOKIE_PATH)):
            response.delete_cookie(
                key, path=path, secure=secure, httponly=True, samesite="strict"
            )

    def logout(self) -> RedirectResponse:
        """Clear the session cookies and redirect to the post-logout URL."""
        response = RedirectResponse(
            url=oidc_config().post_logout_redirect_url, status_code=303
        )
        self.clear_session_cookies(response)
        return response


def _seconds_until_expiry(token: str) -> int | None:
    """Seconds until a JWT's `exp`, read unverified; `None` when it states none.

    Unverified is enough: the token came straight from the token endpoint, and only
    this client's secret redeems it. `None` makes the cookie last the browser session.
    """
    try:
        exp = jwt.decode(token, options={"verify_signature": False}).get("exp")
    except jwt.PyJWTError:
        return None
    if not isinstance(exp, (int, float)):
        return None
    return max(0, int(exp - time.time()))
