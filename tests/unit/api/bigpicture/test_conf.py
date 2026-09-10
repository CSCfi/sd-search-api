"""Tests for the Bigpicture deployment configuration."""

import base64

import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import (
    BestAvailableEncryption,
    Encoding,
    PrivateFormat,
)

from search_api.api.bigpicture.conf import BigpictureRemoteConfiguration
from tests.utils.keys import base64_pem_private_key, pem_key_pair

AUDIENCE = "https://submitter.example/api/sync"


def _encrypted_pem_private_key(passphrase: bytes) -> bytes:
    """A PEM-encoded EC private key that cannot be read without its passphrase."""

    return ec.generate_private_key(ec.SECP256R1()).private_bytes(
        Encoding.PEM, PrivateFormat.PKCS8, BestAvailableEncryption(passphrase)
    )


def _config(private_key: str) -> BigpictureRemoteConfiguration:
    return BigpictureRemoteConfiguration(
        BP_SUBMIT_API_URL="https://submitter.example/api",
        BP_SUBMIT_PRIVATE_KEY=private_key,
        BP_SUBMIT_AUDIENCE=AUDIENCE,
    )


def test_private_key_is_decoded() -> None:
    private_key, _ = pem_key_pair()

    config = _config(base64.b64encode(private_key.encode("utf-8")).decode("ascii"))

    assert config.BP_SUBMIT_PRIVATE_KEY == private_key


def test_default_issuer() -> None:
    config = _config(base64_pem_private_key())

    assert config.BP_SUBMIT_ISSUER == "sd-search-api"


def test_private_not_base64() -> None:
    with pytest.raises(ValueError, match="base64"):
        _config("not base64")


@pytest.mark.parametrize(
    "decoded_key",
    [b"not a key", _encrypted_pem_private_key(b"passphrase")],
    ids=["not a key at all", "a key locked with a passphrase"],
)
def test_private_key_not_pem(decoded_key: bytes) -> None:
    with pytest.raises(ValueError, match="PEM private key"):
        _config(base64.b64encode(decoded_key).decode("ascii"))
