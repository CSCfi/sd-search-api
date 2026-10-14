"""Validation of the identity provider's access tokens, the session of this API."""

import asyncio
import math
import threading
import time
from typing import Any

import jwt
from jwt import PyJWK, PyJWKClient, PyJWKSet

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
        # Read once, with the discovery document: not from the environment per request.
        self._audience = ""
        # The cached key set PyJWKClient last held, and its signing keys by `kid`:
        # PyJWKClient would parse every key afresh on each lookup.
        self._parsed: tuple[Any, dict[str, PyJWK]] | None = None
        self._last_refetch = -math.inf
        # The lookup runs in worker threads, which must not both take one refetch slot.
        self._refetch_lock = threading.Lock()

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
            self._audience = oidc_config().OIDC_CLIENT_ID

        try:
            signing_key = self._cached_signing_key(header.get("kid"))
            if signing_key is None:
                # A fetch is due: PyJWKClient fetches with blocking urllib.
                signing_key = await asyncio.to_thread(
                    self._find_signing_key, self._jwks_client, header.get("kid")
                )
        except InvalidAccessToken:
            raise  # the token names a key the valid key set does not have
        except (jwt.PyJWTError, ValueError) as exc:
            # Unreachable, or not a usable key set (an HTML error page, JSON that is no
            # key set, no signing keys): the provider's fault, never the token's.
            raise SigningKeysUnavailable("OIDC signing keys unusable.") from exc

        try:
            claims = jwt.decode(
                token,
                signing_key,
                algorithms=[algorithm],
                issuer=metadata["issuer"],
                audience=self._audience,
                options={"require": ["exp", "sub"]},
            )
        except jwt.PyJWTError as exc:
            raise InvalidAccessToken(str(exc)) from exc

        return str(claims["sub"])

    def _cached_signing_key(self, kid: str | None) -> PyJWK | None:
        """The key named `kid` in the cached key set, or `None` when a fetch is due.

        Reads PyJWKClient's in-memory cache, which never fetches, so the steady state
        stays on the event loop. Anything unusable in the cache is left to the slow
        path, which reports it.
        """
        if kid is None:
            return None  # no signing key lacks a `kid`, so the slow path refuses it
        cache = self._jwks_client.jwk_set_cache if self._jwks_client else None
        data: Any = cache.get() if cache else None  # the raw JSON, until it expires
        if not isinstance(data, dict):
            return None
        parsed = self._parsed
        if parsed is None or parsed[0] is not data:
            try:
                keys = PyJWKSet.from_dict(data).keys
            except jwt.PyJWTError:
                return None
            # The keys PyJWKClient counts as signing keys: `sig` or unspecified, with a `kid`.
            signing = {
                k.key_id: k
                for k in keys
                if k.public_key_use in ("sig", None) and k.key_id
            }
            parsed = self._parsed = (data, signing)
        return parsed[1].get(kid)

    def _find_signing_key(self, client: PyJWKClient, kid: str | None) -> PyJWK:
        """Find the key named `kid`, refetching the key set at most once a minute."""
        for refetch in (False, True):
            if refetch:
                with self._refetch_lock:
                    now = time.monotonic()
                    if now - self._last_refetch < JWKS_MIN_REFETCH_INTERVAL:
                        # Not looked up yet, so not the token's fault yet: it may be
                        # signed with a key just rotated in. A 503 does not log the
                        # user out, as a 401 would, and the next slot fetches it.
                        raise SigningKeysUnavailable("Signing key not yet fetched.")
                    self._last_refetch = now
            for key in client.get_signing_keys(refresh=refetch):
                if key.key_id == kid:
                    return key
        raise InvalidAccessToken("No matching signing key.")
