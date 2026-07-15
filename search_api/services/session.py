"""Session JWT issuance and validation for the OIDC relying party."""

from datetime import datetime, timedelta, timezone
from typing import Any

import jwt

from search_api.conf import jwt_config

JWT_EXPIRATION = timedelta(days=7)


def create_jwt_token(
    user_id: str, user_name: str, expiration: timedelta = JWT_EXPIRATION
) -> str:
    """Create a signed session JWT for the given user."""
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "user_name": user_name,
        "iat": now,
        "exp": now + expiration,
        "iss": jwt_config().JWT_ISSUER,
    }
    return jwt.encode(
        payload, jwt_config().JWT_KEY, algorithm=jwt_config().JWT_ALGORITHM
    )


def create_jwt_token_from_userinfo(userinfo: dict[str, Any]) -> str:
    """Create a signed session JWT from an OIDC `/userinfo` response."""
    if "sub" not in userinfo:
        raise ValueError("userinfo is missing the required 'sub' claim")
    user_id = userinfo["sub"]

    given_name = userinfo.get("given_name", "").strip()
    family_name = userinfo.get("family_name", "").strip()
    user_name = f"{given_name} {family_name}".strip() or user_id

    return create_jwt_token(user_id, user_name)


def validate_jwt_token(token: str) -> tuple[str, str]:
    """Decode and verify a session JWT, returning `(user_id, user_name)`."""
    decoded = jwt.decode(
        token,
        jwt_config().JWT_KEY,
        algorithms=[jwt_config().JWT_ALGORITHM],
        issuer=jwt_config().JWT_ISSUER,
        # Enforce presence, not just format: PyJWT only checks "exp" when it's in the
        # payload, and without "sub" required here callers would otherwise hit a raw
        # KeyError below instead of a PyJWTError.
        options={"require": ["exp", "sub"]},
    )
    return decoded["sub"], decoded.get("user_name", decoded["sub"])
