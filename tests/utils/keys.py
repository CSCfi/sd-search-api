"""Key pairs for the tests that sign or verify service tokens."""

import base64

from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)


def pem_key_pair() -> tuple[str, str]:
    """
    Generate a key pair for signing service tokens.

    :return: The PEM-encoded EC private key to sign with, and public key to verify with.
    """

    key = ec.generate_private_key(ec.SECP256R1())
    private_key = key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())
    public_key = key.public_key().public_bytes(
        Encoding.PEM, PublicFormat.SubjectPublicKeyInfo
    )
    return private_key.decode("utf-8"), public_key.decode("utf-8")


def base64_pem_private_key() -> str:
    """
    Generate a private key encoded the way it is configured.

    :return: The base64-encoded PEM EC private key.
    """

    private_key, _ = pem_key_pair()
    return base64.b64encode(private_key.encode("utf-8")).decode("ascii")
