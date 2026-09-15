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


def bigpicture_local_config() -> BigpictureLocalConfiguration:
    """Get the Bigpicture loading local configuration."""
    return BigpictureLocalConfiguration()
