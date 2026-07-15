"""Unit tests for search_api.conf."""

import base64

import pytest

from search_api.conf import JWTConfiguration


def test_jwt_key_below_minimum_length_rejected():
    short_key = base64.b64encode(b"too-short").decode("ascii")
    with pytest.raises(ValueError, match="at least 32 bytes"):
        JWTConfiguration(JWT_KEY=short_key)


def test_jwt_key_at_minimum_length_accepted():
    key = base64.b64encode(b"x" * 32).decode("ascii")
    config = JWTConfiguration(JWT_KEY=key)
    assert config.JWT_KEY == "x" * 32
