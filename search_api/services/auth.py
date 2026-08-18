"""OIDC relying party service."""

import asyncio
import logging
from typing import Any

from cryptojwt import KeyJar  # type: ignore[import-untyped]
from fastapi import HTTPException
from idpyoidc.client.exception import OidcServiceError  # type: ignore[import-untyped]
from idpyoidc.client.rp_handler import RPHandler  # type: ignore[import-untyped]
from idpyoidc.exception import OidcMsgError  # type: ignore[import-untyped]
from requests.exceptions import RequestException
from starlette.responses import RedirectResponse

from search_api.conf import oidc_config
from search_api.exceptions import SystemException
from search_api.services.session import JWT_EXPIRATION, create_jwt_token_from_userinfo

SESSION_COOKIE = "access_token"

# idpyoidc's RPHandler logs the client config -- including OIDC_CLIENT_SECRET -- at
# DEBUG on init. Capped here, independent of whatever level the app's own root logger
# ends up at, so raising verbosity elsewhere can never leak the secret.
logging.getLogger("idpyoidc").setLevel(logging.INFO)


class AuthServiceHandler:
    """OIDC Authorization Code + PKCE relying party, backed by idpyoidc's `RPHandler`."""

    def __init__(self) -> None:
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

    async def callback(self, state: str, code: str) -> str:
        """Exchange the authorization code for tokens and return a signed JWT for the session."""
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

            jwt_token = create_jwt_token_from_userinfo(session["userinfo"])

        return jwt_token

    def initiate_web_session(self, jwt_token: str) -> RedirectResponse:
        """Set the session cookie and redirect to the post-login URL."""
        response = RedirectResponse(url=oidc_config().redirect_url, status_code=303)
        response.set_cookie(
            key=SESSION_COOKIE,
            value=jwt_token,
            httponly=True,
            secure=oidc_config().OIDC_SECURE_COOKIE,
            samesite="strict",
            path="/",
            max_age=int(JWT_EXPIRATION.total_seconds()),
        )
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        return response

    def logout(self) -> RedirectResponse:
        """Clear the session cookie and redirect to the post-logout URL."""
        response = RedirectResponse(
            url=oidc_config().post_logout_redirect_url, status_code=303
        )
        response.delete_cookie(
            SESSION_COOKIE,
            path="/",
            secure=oidc_config().OIDC_SECURE_COOKIE,
            httponly=True,
            samesite="strict",
        )
        return response
