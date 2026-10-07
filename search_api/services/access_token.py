"""Validation of the identity provider's access tokens, the session of this API."""

import asyncio
import math
import time

import jwt
from jwt import PyJWK, PyJWKClient, PyJWKClientConnectionError

from search_api.conf import oidc_config
from search_api.exceptions import SystemException
from search_api.services.oidc_metadata import ProviderMetadata

# Asymmetric only: `none` and every HMAC algorithm are refused before any key lookup,
# which also rejects the HS256 session tokens this API minted before.
ALLOWED_ALGORITHMS = frozenset(
    {
        "RS256",
        "RS384",
        "RS512",
        "PS256",
        "PS384",
        "PS512",
        "ES256",
        "ES384",
        "ES512",
        "EdDSA",
    }
)

# RFC 9068: an access token's `typ` is `at+jwt`, which is what tells it apart from an ID
# token or a refresh token signed with the very same key.
ACCESS_TOKEN_TYPES = frozenset({"at+jwt", "application/at+jwt"})

# A key not in the cache triggers a refetch, so rotation does not wait for the cache to
# expire; a long lifespan only keeps validation off the issuer. Refetches are spaced, as
# anyone can send a token naming a key that does not exist.
JWKS_CACHE_LIFESPAN = 24 * 3600
JWKS_MIN_REFETCH_INTERVAL = 60.0
JWKS_TIMEOUT = 10.0


class InvalidAccessToken(Exception):
    """The token is not a valid access token for this API."""


class SigningKeysUnavailable(SystemException):
    """The issuer's signing keys could not be fetched."""


class AccessTokenValidator:
    """Validates access tokens locally against the issuer's published signing keys."""

    def __init__(self, metadata: ProviderMetadata) -> None:
        self._metadata = metadata
        self._jwks_client: PyJWKClient | None = None
        self._last_refetch = -math.inf

    async def validate(self, token: str) -> str:
        """Return the token's `sub`.

        Raises `InvalidAccessToken` for any token that is not a valid access token for
        this API, and `SystemException` when the issuer is needed and unreachable.
        """
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError as exc:
            raise InvalidAccessToken("Not a JWT.") from exc

        algorithm = header.get("alg")
        if algorithm not in ALLOWED_ALGORITHMS:
            raise InvalidAccessToken(f"Algorithm {algorithm!r} is not accepted.")
        if str(header.get("typ", "")).lower() not in ACCESS_TOKEN_TYPES:
            raise InvalidAccessToken("Not an access token.")

        metadata = await self._metadata.get()
        if self._jwks_client is None:
            self._jwks_client = PyJWKClient(
                metadata["jwks_uri"],
                cache_jwk_set=True,
                lifespan=JWKS_CACHE_LIFESPAN,
                timeout=JWKS_TIMEOUT,
            )

        try:
            # PyJWKClient fetches with blocking urllib, on a cache miss.
            signing_key = await asyncio.to_thread(
                self._find_signing_key, self._jwks_client, header.get("kid")
            )
        except PyJWKClientConnectionError as exc:
            raise SigningKeysUnavailable("OIDC signing keys unreachable.") from exc
        except jwt.PyJWTError as exc:
            raise InvalidAccessToken("No usable signing keys.") from exc

        try:
            claims = jwt.decode(
                token,
                signing_key,
                algorithms=[algorithm],
                issuer=metadata["issuer"],
                audience=oidc_config().OIDC_CLIENT_ID,
                options={"require": ["exp", "sub"]},
            )
        except jwt.PyJWTError as exc:
            raise InvalidAccessToken(str(exc)) from exc

        return str(claims["sub"])

    def _find_signing_key(self, client: PyJWKClient, kid: str | None) -> PyJWK:
        """Find the key named `kid`, refetching the key set at most once a minute."""
        for refetch in (False, True):
            if refetch:
                if time.monotonic() - self._last_refetch < JWKS_MIN_REFETCH_INTERVAL:
                    break
                self._last_refetch = time.monotonic()
            for key in client.get_signing_keys(refresh=refetch):
                if key.key_id == kid:
                    return key
        raise InvalidAccessToken("No matching signing key.")
