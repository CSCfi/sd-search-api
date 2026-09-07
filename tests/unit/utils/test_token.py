"""Tests for signing service JWT token."""

from datetime import timedelta

import jwt
import pytest

from search_api.utils.token import (
    SERVICE_TOKEN_ALGORITHM,
    SERVICE_TOKEN_LIFETIME,
    sign_service_token,
)
from tests.utils.keys import pem_key_pair

ISSUER = "sd-search-api"
AUDIENCE = "https://submitter.example/api/sync"


def _verify(token: str, public_key: str) -> dict[str, object]:
    """Verify a token the way the receiving service does."""

    return jwt.decode(
        token,
        key=public_key,
        algorithms=[SERVICE_TOKEN_ALGORITHM],
        audience=AUDIENCE,
        issuer=ISSUER,
        options={"require": ["iss", "sub", "aud", "iat", "exp", "jti"]},
    )


def test_sign_service_token() -> None:
    private_key, public_key = pem_key_pair()

    claims = _verify(
        sign_service_token(private_key=private_key, issuer=ISSUER, audience=AUDIENCE),
        public_key,
    )

    assert claims["iss"] == ISSUER
    assert claims["sub"] == ISSUER
    assert claims["aud"] == AUDIENCE
    assert claims["exp"] - claims["iat"] == SERVICE_TOKEN_LIFETIME.total_seconds()


def test_sign_service_token_lifetime() -> None:
    private_key, public_key = pem_key_pair()

    claims = _verify(
        sign_service_token(
            private_key=private_key,
            issuer=ISSUER,
            audience=AUDIENCE,
            lifetime=timedelta(seconds=10),
        ),
        public_key,
    )

    assert claims["exp"] - claims["iat"] == 10


def test_sign_service_token_jti_unique() -> None:
    private_key, public_key = pem_key_pair()

    tokens = [
        sign_service_token(private_key=private_key, issuer=ISSUER, audience=AUDIENCE)
        for _ in range(2)
    ]

    assert len({_verify(token, public_key)["jti"] for token in tokens}) == 2


def test_sign_service_token_rejected_for_another_audience() -> None:
    private_key, public_key = pem_key_pair()

    token = sign_service_token(
        private_key=private_key, issuer=ISSUER, audience="https://elsewhere.example"
    )

    with pytest.raises(jwt.InvalidAudienceError):
        _verify(token, public_key)


def test_sign_service_token_rejected_for_another_key() -> None:
    private_key, _ = pem_key_pair()
    _, other_public_key = pem_key_pair()

    token = sign_service_token(
        private_key=private_key, issuer=ISSUER, audience=AUDIENCE
    )

    with pytest.raises(jwt.InvalidSignatureError):
        _verify(token, other_public_key)


def test_sign_service_token_expires() -> None:
    private_key, public_key = pem_key_pair()

    token = sign_service_token(
        private_key=private_key,
        issuer=ISSUER,
        audience=AUDIENCE,
        lifetime=timedelta(seconds=-1),
    )

    with pytest.raises(jwt.ExpiredSignatureError):
        _verify(token, public_key)
