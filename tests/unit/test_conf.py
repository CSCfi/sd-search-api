"""Unit tests for search_api.conf."""

import base64

import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import (
    BestAvailableEncryption,
    Encoding,
    PrivateFormat,
)

from search_api.conf import (
    AIConfiguration,
    OIDCConfiguration,
    SdSubmitSyncConfiguration,
)
from tests.utils.keys import base64_pem_private_key, pem_key_pair


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


def test_default_scope_asks_for_a_refresh_token(monkeypatch):
    monkeypatch.delenv("OIDC_SCOPE", raising=False)
    assert "offline_access" in _oidc_config().OIDC_SCOPE.split()


_SD_SUBMIT_AUDIENCE = "https://submitter.example/api/sync"


def _encrypted_pem_private_key(passphrase: bytes) -> bytes:
    """A PEM-encoded EC private key that cannot be read without its passphrase."""

    return ec.generate_private_key(ec.SECP256R1()).private_bytes(
        Encoding.PEM, PrivateFormat.PKCS8, BestAvailableEncryption(passphrase)
    )


def _sd_submit_sync_config(private_key: str) -> SdSubmitSyncConfiguration:
    return SdSubmitSyncConfiguration(
        SD_SUBMIT_API_URL="https://submitter.example/api",
        SD_SUBMIT_PRIVATE_KEY=private_key,
        SD_SUBMIT_AUDIENCE=_SD_SUBMIT_AUDIENCE,
    )


def test_sd_submit_sync_private_key_is_decoded() -> None:
    private_key, _ = pem_key_pair()

    config = _sd_submit_sync_config(
        base64.b64encode(private_key.encode("utf-8")).decode("ascii")
    )

    assert config.SD_SUBMIT_PRIVATE_KEY == private_key


def test_sd_submit_sync_default_issuer() -> None:
    config = _sd_submit_sync_config(base64_pem_private_key())

    assert config.SD_SUBMIT_ISSUER == "sd-search-api"


def test_sd_submit_sync_private_key_not_base64() -> None:
    with pytest.raises(ValueError, match="base64"):
        _sd_submit_sync_config("not base64")


@pytest.mark.parametrize(
    "decoded_key",
    [b"not a key", _encrypted_pem_private_key(b"passphrase")],
    ids=["not a key at all", "a key locked with a passphrase"],
)
def test_sd_submit_sync_private_key_not_pem(decoded_key: bytes) -> None:
    with pytest.raises(ValueError, match="PEM private key"):
        _sd_submit_sync_config(base64.b64encode(decoded_key).decode("ascii"))


def test_ai_openai_provider_requires_base_url_and_api_key(monkeypatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("LLM_MODEL", "qwen2.5:7b")
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    with pytest.raises(ValueError, match="LLM_BASE_URL and LLM_API_KEY are required"):
        AIConfiguration()


def test_ai_other_provider_needs_no_base_url_or_api_key(monkeypatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("LLM_MODEL", "claude-sonnet-5-5")
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    assert AIConfiguration().LLM_PROVIDER == "anthropic"


def test_ai_model_has_no_default(monkeypatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.delenv("LLM_MODEL", raising=False)
    with pytest.raises(ValueError, match="LLM_MODEL"):
        AIConfiguration()
