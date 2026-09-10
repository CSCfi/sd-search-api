"""Tests for publishing the public keys this deployment signs with."""

from collections.abc import Callable
from typing import Any

import jwt
from fastapi import FastAPI
from fastapi.testclient import TestClient

from search_api.api.jwk.routes import JWK_PATH, make_jwk_router
from search_api.utils.token import (
    SERVICE_TOKEN_ALGORITHM,
    public_key_jwk,
    sign_service_token,
)
from tests.utils.keys import pem_key_pair

AUDIENCE = "https://submitter.example/api/sync"
ISSUER = "sd-search-api"

PRIVATE_KEY, _ = pem_key_pair()


def _jwt_request(public_jwks: Callable[[], list[dict[str, Any]]] | None) -> Any:
    """The response of a /jwt request."""

    app = FastAPI()
    app.include_router(make_jwk_router(public_jwks))
    return TestClient(app).get(JWK_PATH)


def test_jwk_verify_signed_token() -> None:
    """The public key can be used to verify a signed JWT token."""

    keys = _jwt_request(lambda: [public_key_jwk(PRIVATE_KEY)]).json()["keys"]

    claims = jwt.decode(
        sign_service_token(private_key=PRIVATE_KEY, issuer=ISSUER, audience=AUDIENCE),
        key=jwt.PyJWK(keys[0]).key,
        algorithms=[SERVICE_TOKEN_ALGORITHM],
        audience=AUDIENCE,
        issuer=ISSUER,
    )

    assert claims["iss"] == ISSUER


def test_jwk_publishes_no_private_key() -> None:
    assert (
        "d" not in _jwt_request(lambda: [public_key_jwk(PRIVATE_KEY)]).json()["keys"][0]
    )


def test_jwk_no_configured_key() -> None:
    assert _jwt_request(list).json() == {"keys": []}


def test_jwk_no_signing() -> None:
    assert _jwt_request(None).status_code == 404
