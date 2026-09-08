"""Signing of JWT tokens."""

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from joserfc.jwk import ECKey

# A JWT token is created for each request, so only a short lifetime is required.
SERVICE_TOKEN_LIFETIME = timedelta(seconds=60)

# SIgn the JWT token using a private key. The public key is sent to the recipient.
SERVICE_TOKEN_ALGORITHM = "ES256"


def sign_service_token(
    *,
    private_key: str,
    issuer: str,
    audience: str,
    lifetime: timedelta = SERVICE_TOKEN_LIFETIME,
) -> str:
    """
    Sign a JWT token that identifies this service to the service it is calling.

    The claims are the ones RFC 7523 defines for authenticating a client with a signed JWT.

    :param private_key: The PEM-encoded EC private key to sign with.
    :param issuer: The 'iss' and 'sub' claims, both naming this service.
    :param audience: The 'aud' claim, naming the API the token is for. The receiver rejects a
        token issued for anything else, so one cannot be replayed at another service that
        trusts the same key.
    :param lifetime: How long the token remains valid.
    :return: The signed token.
    """

    issued_at = datetime.now(timezone.utc)
    claims = {
        "iss": issuer,
        "sub": issuer,
        "aud": audience,
        "iat": issued_at,
        "exp": issued_at + lifetime,
        # Unique token ID to allow tokens to be individually tracked.
        "jti": uuid.uuid4().hex,
    }
    return jwt.encode(claims, private_key, algorithm=SERVICE_TOKEN_ALGORITHM)


def public_key_jwk(private_key: str) -> dict[str, Any]:
    """
    Derive the public key of a signing key as a JWK.

    :param private_key: The PEM-encoded EC private key the tokens are signed with.
    :raises ValueError: if the key is not an EC private key.
    :return: The public key as a JWK, named by its RFC 7638 thumbprint.
    """

    key = ECKey.import_key(private_key, {"alg": SERVICE_TOKEN_ALGORITHM, "use": "sig"})

    # Only the public key is published.
    jwk: dict[str, Any] = key.as_dict(private=False)
    return {**jwk, "kid": key.thumbprint()}
