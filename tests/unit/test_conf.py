"""Unit tests for search_api.conf."""

import base64

import pytest

from search_api.conf import JWTConfiguration, OIDCConfiguration


def test_jwt_key_below_minimum_length_rejected():
    short_key = base64.b64encode(b"too-short").decode("ascii")
    with pytest.raises(ValueError, match="at least 32 bytes"):
        JWTConfiguration(JWT_KEY=short_key)


def test_jwt_key_at_minimum_length_accepted():
    key = base64.b64encode(b"x" * 32).decode("ascii")
    config = JWTConfiguration(JWT_KEY=key)
    assert config.JWT_KEY == "x" * 32


def _oidc_config(**overrides: str) -> OIDCConfiguration:
    return OIDCConfiguration(
        BASE_URL="http://localhost:8000",
        OIDC_URL="http://localhost:9999",
        OIDC_CLIENT_ID="test-client-id",
        OIDC_CLIENT_SECRET="test-client-secret",
        **overrides,
    )


def test_post_logout_redirect_url_drops_path_from_redirect_url():
    config = _oidc_config(
        OIDC_REDIRECT_URL="https://bp-sd-search-ui.example.org/search"
    )
    assert config.post_logout_redirect_url == "https://bp-sd-search-ui.example.org/"


def test_post_logout_redirect_url_falls_back_to_base_url_origin():
    config = _oidc_config()
    assert config.redirect_url == "http://localhost:8000/docs"
    assert config.post_logout_redirect_url == "http://localhost:8000/"
