"""Unit tests for search_api.services.session."""

import os
from base64 import b64encode
from datetime import timedelta

import jwt
import pytest

os.environ["JWT_KEY"] = b64encode(
    b"test-jwt-signing-key-at-least-32-bytes-long"
).decode("ascii")
os.environ["JWT_ISSUER"] = "sd-search-api-test"
os.environ["JWT_ALGORITHM"] = "HS256"

from search_api.services.session import (
    create_jwt_token,
    create_jwt_token_from_userinfo,
    validate_jwt_token,
)


def test_create_and_validate_jwt_token_round_trip():
    token = create_jwt_token("user-1", "Jane Doe")
    user_id, user_name = validate_jwt_token(token)
    assert user_id == "user-1"
    assert user_name == "Jane Doe"


def test_create_jwt_token_from_userinfo_builds_user_name():
    userinfo = {"sub": "user-1", "given_name": "Jane", "family_name": "Doe"}
    token = create_jwt_token_from_userinfo(userinfo)
    user_id, user_name = validate_jwt_token(token)
    assert user_id == "user-1"
    assert user_name == "Jane Doe"


def test_create_jwt_token_from_userinfo_falls_back_to_sub_as_name():
    userinfo = {"sub": "user-1", "given_name": "", "family_name": ""}
    token = create_jwt_token_from_userinfo(userinfo)
    user_id, user_name = validate_jwt_token(token)
    assert user_id == "user-1"
    assert user_name == "user-1"


def test_create_jwt_token_from_userinfo_missing_sub_raises():
    with pytest.raises(ValueError):
        create_jwt_token_from_userinfo({"given_name": "Jane"})


def test_validate_jwt_token_expired_raises():
    token = create_jwt_token("user-1", "Jane Doe", expiration=timedelta(seconds=-1))
    with pytest.raises(jwt.ExpiredSignatureError):
        validate_jwt_token(token)


def test_validate_jwt_token_wrong_key_raises(monkeypatch):
    token = create_jwt_token("user-1", "Jane Doe")
    monkeypatch.setenv(
        "JWT_KEY",
        b64encode(b"a-different-signing-key-at-least-32-bytes-long").decode("ascii"),
    )
    with pytest.raises(jwt.InvalidSignatureError):
        validate_jwt_token(token)


def test_validate_jwt_token_wrong_issuer_raises(monkeypatch):
    token = create_jwt_token("user-1", "Jane Doe")
    monkeypatch.setenv("JWT_ISSUER", "some-other-issuer")
    with pytest.raises(jwt.InvalidIssuerError):
        validate_jwt_token(token)


def test_validate_jwt_token_missing_exp_raises():
    from search_api.conf import jwt_config

    cfg = jwt_config()
    token = jwt.encode(
        {"sub": "user-1", "iss": cfg.JWT_ISSUER},
        cfg.JWT_KEY,
        algorithm=cfg.JWT_ALGORITHM,
    )
    with pytest.raises(jwt.MissingRequiredClaimError):
        validate_jwt_token(token)


def test_validate_jwt_token_missing_sub_raises():
    from datetime import datetime, timezone

    from search_api.conf import jwt_config

    cfg = jwt_config()
    now = datetime.now(timezone.utc)
    token = jwt.encode(
        {"iss": cfg.JWT_ISSUER, "exp": now + timedelta(days=1)},
        cfg.JWT_KEY,
        algorithm=cfg.JWT_ALGORITHM,
    )
    with pytest.raises(jwt.MissingRequiredClaimError):
        validate_jwt_token(token)
