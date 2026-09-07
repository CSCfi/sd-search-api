"""Bigpicture deployment configuration."""

import base64

from cryptography.hazmat.primitives.serialization import load_pem_private_key
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings


class BigpictureLocalConfiguration(BaseSettings):
    """Configuration for reading Bigpicture submissions from a directory."""

    BP_C4GH_KEY_FILE: str | None = Field(
        default=None,
        description=(
            "Path to a Crypt4GH private key file (.sec) for decrypting .c4gh files. "
            "Unset when the source material is not encrypted."
        ),
    )
    BP_C4GH_PASSPHRASE: str | None = Field(
        default=None,
        description="Passphrase of the Crypt4GH private key, unset for an unprotected key.",
    )


class BigpictureRemoteConfiguration(BaseSettings):
    """Configuration for fetching Bigpicture submissions from the SD submit API."""

    BP_SUBMIT_API_URL: str = Field(description="Bigpicture submit API base URL.")
    BP_SUBMIT_PRIVATE_KEY: str = Field(
        description=(
            "Base64-encoded PEM EC private key to sign the Bigpicture submit API JWT token. "
            "The submit API has the public key to verify the JWT token ."
        )
    )
    BP_SUBMIT_AUDIENCE: str = Field(
        description=(
            "Audience of the Bigpicture submit API JWT tokens. Must be the audience "
            "the API expects, or the JWT token is rejected."
        )
    )
    BP_SUBMIT_ISSUER: str = Field(
        default="sd-search-api",
        description="Issuer of the Bigpicture submit API tokens, naming this service.",
    )

    @field_validator("BP_SUBMIT_PRIVATE_KEY")
    @classmethod
    def decode_private_key(cls, value: str) -> str:
        """Decode and parse the base64-encoded PEM private key."""
        try:
            key = base64.b64decode(value, validate=True)
        except Exception as ex:
            raise ValueError(
                "BP_SUBMIT_PRIVATE_KEY must be a valid base64-encoded string"
            ) from ex
        # Parse JWT token here to report errors when the configuration is loaded.
        try:
            load_pem_private_key(key, password=None)
        except Exception as ex:
            raise ValueError(
                "BP_SUBMIT_PRIVATE_KEY must decode to an unencrypted PEM private key"
            ) from ex
        return key.decode("utf-8")


def bigpicture_local_config() -> BigpictureLocalConfiguration:
    """Get the Bigpicture loading local configuration."""
    return BigpictureLocalConfiguration()


def bigpicture_remote_config() -> BigpictureRemoteConfiguration:
    """Get the Bigpicture loading remote configuration."""
    return BigpictureRemoteConfiguration()
