"""Bigpicture deployment configuration."""

from pydantic import Field
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
    BP_SUBMIT_API_KEY: str = Field(description="Bigpicture submit API bearer token.")


def bigpicture_local_config() -> BigpictureLocalConfiguration:
    """Get the Bigpicture loading local configuration."""
    return BigpictureLocalConfiguration()


def bigpicture_remote_config() -> BigpictureRemoteConfiguration:
    """Get the Bigpicture loading remote configuration."""
    return BigpictureRemoteConfiguration()
