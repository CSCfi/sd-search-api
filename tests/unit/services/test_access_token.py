"""Unit tests for search_api.services.access_token."""

import io
import json
import os
import time
from typing import Any
from urllib.error import URLError

os.environ["BASE_URL"] = "http://localhost:8000"
os.environ["OIDC_URL"] = "http://localhost:9999"
os.environ["OIDC_CLIENT_ID"] = "test-client-id"
os.environ["OIDC_CLIENT_SECRET"] = "test-client-secret"

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from jwt import jwks_client
from jwt.algorithms import ECAlgorithm, RSAAlgorithm

import search_api.services.access_token as access_token_module

from search_api.exceptions import SystemException
from search_api.services.access_token import (
    AccessTokenValidator,
    InvalidAccessToken,
    SigningKeysUnavailable,
)

ISSUER = "https://issuer.example/oidc/"
CLIENT_ID = "test-client-id"
SUB = "user-1@lifescience-ri.eu"

KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _jwk(private_key: Any, kid: str, algorithm: str = "RS256") -> dict[str, Any]:
    to_jwk = ECAlgorithm.to_jwk if algorithm.startswith("ES") else RSAAlgorithm.to_jwk
    jwk = to_jwk(private_key.public_key(), as_dict=True)
    return {**jwk, "kid": kid, "use": "sig", "alg": algorithm}


def _token(
    *,
    key: Any = KEY,
    kid: str = "rsa1",
    typ: str = "at+jwt",
    algorithm: str = "RS256",
    **overrides: Any,
) -> str:
    now = int(time.time())
    claims = {
        "iss": ISSUER,
        "aud": CLIENT_ID,
        "sub": SUB,
        "client_id": CLIENT_ID,
        "scope": "openid profile email",
        "iat": now,
        "exp": now + 3600,
        "jti": "jti-1",
    }
    claims.update(overrides)
    claims = {k: v for k, v in claims.items() if v is not None}
    return jwt.encode(
        claims, key, algorithm=algorithm, headers={"kid": kid, "typ": typ}
    )


class FakeMetadata:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error

    async def get(self) -> dict[str, Any]:
        if self.error:
            raise self.error
        return {
            "issuer": ISSUER,
            "jwks_uri": "https://issuer.example/oidc/jwk",
            "token_endpoint": "https://issuer.example/oidc/token",
        }


@pytest.fixture
def jwks(monkeypatch):
    """Stub the JWKS endpoint, below PyJWKClient's own caching.

    Each fetch answers with the next entry of `responses`, the last one repeating; an
    exception entry is raised, as `urlopen` would.
    """
    state: dict[str, Any] = {"responses": [{"keys": [_jwk(KEY, "rsa1")]}], "fetches": 0}

    def urlopen(request, timeout=None, context=None):
        state["fetches"] += 1
        responses = state["responses"]
        response = responses[0] if len(responses) == 1 else responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return io.BytesIO(json.dumps(response).encode())

    monkeypatch.setattr(jwks_client.urllib.request, "urlopen", urlopen)
    return state


def _validator(metadata: FakeMetadata | None = None) -> AccessTokenValidator:
    return AccessTokenValidator(metadata or FakeMetadata())  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_valid_access_token_returns_sub(jwks):
    assert await _validator().validate(_token()) == SUB


@pytest.mark.asyncio
async def test_typ_is_case_insensitive_and_accepts_media_type(jwks):
    validator = _validator()
    assert await validator.validate(_token(typ="AT+JWT")) == SUB
    assert await validator.validate(_token(typ="application/at+jwt")) == SUB


@pytest.mark.asyncio
async def test_audience_may_be_a_list_containing_the_client(jwks):
    token = _token(aud=["another-resource", CLIENT_ID])
    assert await _validator().validate(token) == SUB


@pytest.mark.asyncio
async def test_non_rsa_signing_key_is_accepted(jwks):
    ec_key = ec.generate_private_key(ec.SECP256R1())
    jwks["responses"] = [{"keys": [_jwk(ec_key, "ec1", "ES256")]}]

    token = _token(key=ec_key, kid="ec1", algorithm="ES256")

    assert await _validator().validate(token) == SUB


@pytest.mark.asyncio
async def test_keys_are_cached_between_validations(jwks):
    validator = _validator()
    await validator.validate(_token())
    await validator.validate(_token())
    assert jwks["fetches"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "claims",
    [
        {"exp": int(time.time()) - 60, "iat": int(time.time()) - 3660},
        {"aud": "another-client"},
        # OIDC_URL's trailing slash matters: the discovered issuer is the reference.
        {"iss": ISSUER.rstrip("/")},
        {"sub": None},
        {"exp": None},
    ],
    ids=["expired", "other-audience", "other-issuer", "no-sub", "no-exp"],
)
async def test_invalid_claims_are_rejected(jwks, claims):
    with pytest.raises(InvalidAccessToken):
        await _validator().validate(_token(**claims))


@pytest.mark.asyncio
async def test_tampered_signature_is_rejected(jwks):
    header, payload, _ = _token().split(".")
    forged = _token(key=OTHER_KEY).split(".")[2]
    with pytest.raises(InvalidAccessToken):
        await _validator().validate(f"{header}.{payload}.{forged}")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "token",
    [
        # ID token and refresh token: same key, iss and aud, but not access tokens.
        _token(typ="JWT"),
        _token(typ=""),
        # The HS256 session tokens this API minted before.
        jwt.encode(
            {"sub": SUB, "exp": int(time.time()) + 600},
            "x" * 32,
            algorithm="HS256",
            headers={"typ": "at+jwt"},
        ),
        _token(algorithm="none", key=None),
        "mock-access-token-opaque",
    ],
    ids=["typ-JWT", "no-typ", "hs256", "alg-none", "not-a-jwt"],
)
async def test_rejected_before_any_key_lookup(jwks, token):
    with pytest.raises(InvalidAccessToken):
        await _validator().validate(token)
    assert jwks["fetches"] == 0


@pytest.mark.asyncio
async def test_rotated_key_is_picked_up_by_a_refetch(jwks):
    jwks["responses"] = [
        {"keys": [_jwk(KEY, "rsa1")]},
        {"keys": [_jwk(KEY, "rsa1"), _jwk(OTHER_KEY, "rsa2")]},
    ]
    validator = _validator()
    assert await validator.validate(_token()) == SUB

    assert await validator.validate(_token(key=OTHER_KEY, kid="rsa2")) == SUB
    assert jwks["fetches"] == 2


@pytest.mark.asyncio
async def test_unknown_key_refetches_at_most_once_a_minute(jwks, monkeypatch):
    clock = {"now": 1000.0}
    monkeypatch.setattr(access_token_module.time, "monotonic", lambda: clock["now"])
    validator = _validator()

    with pytest.raises(InvalidAccessToken):
        await validator.validate(_token(kid="no-such-key"))
    assert jwks["fetches"] == 2  # the initial fetch and one refetch

    clock["now"] += 59
    with pytest.raises(InvalidAccessToken):
        await validator.validate(_token(kid="another-missing-key"))
    assert jwks["fetches"] == 2

    clock["now"] += 2
    with pytest.raises(InvalidAccessToken):
        await validator.validate(_token(kid="yet-another-missing-key"))
    assert jwks["fetches"] == 3


@pytest.mark.asyncio
async def test_issuer_outage_spares_tokens_signed_with_cached_keys(jwks):
    jwks["responses"] = [{"keys": [_jwk(KEY, "rsa1")]}, URLError("connection refused")]
    validator = _validator()
    assert await validator.validate(_token()) == SUB

    assert await validator.validate(_token()) == SUB
    with pytest.raises(SigningKeysUnavailable):
        await validator.validate(_token(key=OTHER_KEY, kid="rsa2"))


@pytest.mark.asyncio
async def test_unreachable_keys_raise_signing_keys_unavailable(jwks):
    jwks["responses"] = [URLError("connection refused")]
    with pytest.raises(SigningKeysUnavailable):
        await _validator().validate(_token())


@pytest.mark.asyncio
async def test_unreachable_discovery_raises_system_exception(jwks):
    validator = _validator(FakeMetadata(SystemException("OIDC discovery failed.")))
    with pytest.raises(SystemException):
        await validator.validate(_token())
