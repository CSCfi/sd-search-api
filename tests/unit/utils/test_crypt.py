import io
from unittest.mock import MagicMock

import fsspec
import pytest
from crypt4gh.keys import get_public_key as c4gh_get_public_key
from crypt4gh.keys.c4gh import generate as c4gh_generate
from crypt4gh.lib import encrypt as c4gh_encrypt
from nacl.public import PrivateKey

from search_api.utils.crypt import load_c4gh_keys, read_file, resolve_path


# resolve_path
#


def test_resolve_path_plain_exists():
    """Plain file found — returned as-is."""
    fs = MagicMock()
    fs.exists.side_effect = lambda p: p == "/d/METADATA/dataset.xml"

    result = resolve_path(fs, "/d/METADATA/dataset.xml")

    assert result == "/d/METADATA/dataset.xml"


def test_resolve_path_c4gh_fallback():
    """Plain file missing, .c4gh variant found — .c4gh path returned."""
    fs = MagicMock()
    fs.exists.side_effect = lambda p: p == "/d/METADATA/dataset.xml.c4gh"

    result = resolve_path(fs, "/d/METADATA/dataset.xml")

    assert result == "/d/METADATA/dataset.xml.c4gh"


def test_resolve_path_neither_exists():
    """Neither plain nor .c4gh found — ValueError raised."""
    fs = MagicMock()
    fs.exists.return_value = False

    with pytest.raises(ValueError, match="Missing file"):
        resolve_path(fs, "/d/METADATA/dataset.xml")


# read_file
#


def test_read_file_plain(tmp_path):
    """Plain file is read and returned directly."""
    plain_file = tmp_path / "dataset.xml"
    plain_file.write_bytes(b"<xml/>")

    result = read_file(fsspec.filesystem("file"), str(plain_file), keys=None)

    assert result == b"<xml/>"


def test_read_file_c4gh(tmp_path):
    """.c4gh file is decrypted with a real Crypt4GH key pair."""
    sk = PrivateKey.generate()
    sk_bytes = bytes(sk)
    pk_bytes = bytes(sk.public_key)

    plaintext = b"<xml>real decryption</xml>"
    c4gh_file = tmp_path / "dataset.xml.c4gh"
    with c4gh_file.open("wb") as outfile:
        c4gh_encrypt([(0, sk_bytes, pk_bytes)], io.BytesIO(plaintext), outfile)

    result = read_file(
        fsspec.filesystem("file"), str(c4gh_file), keys=[(0, sk_bytes, None)]
    )

    assert result == plaintext


def test_read_file_c4gh_no_key():
    """.c4gh file without a decryption key raises ValueError."""
    fs = MagicMock()

    with pytest.raises(ValueError, match="no decryption key"):
        read_file(fs, "/d/dataset.xml.c4gh", keys=None)


# load_c4gh_keys
#


def test_load_c4gh_keys_no_passphrase(tmp_path):
    """Unprotected key file: load_c4gh_keys loads it and the keys decrypt real data."""
    seckey_path = tmp_path / "key.sec"
    pubkey_path = tmp_path / "key.pub"
    c4gh_generate(str(seckey_path), str(pubkey_path), b"", b"")

    pk_bytes = c4gh_get_public_key(str(pubkey_path))
    sender_sk = PrivateKey.generate()
    plaintext = b"<xml>no passphrase</xml>"
    c4gh_file = tmp_path / "dataset.xml.c4gh"
    with c4gh_file.open("wb") as outfile:
        c4gh_encrypt([(0, bytes(sender_sk), pk_bytes)], io.BytesIO(plaintext), outfile)

    keys = load_c4gh_keys(str(seckey_path))
    result = read_file(fsspec.filesystem("file"), str(c4gh_file), keys=keys)

    assert result == plaintext


def test_load_c4gh_keys_with_passphrase(tmp_path):
    """Protected key file: load_c4gh_keys unlocks it and the returned keys decrypt real data."""
    passphrase = "s3cr3t"
    seckey_path = tmp_path / "key.sec"
    pubkey_path = tmp_path / "key.pub"

    c4gh_generate(str(seckey_path), str(pubkey_path), passphrase.encode(), b"")

    pk_bytes = c4gh_get_public_key(str(pubkey_path))
    sender_sk = PrivateKey.generate()
    plaintext = b"<xml>passphrase test</xml>"
    c4gh_file = tmp_path / "dataset.xml.c4gh"
    with c4gh_file.open("wb") as outfile:
        c4gh_encrypt([(0, bytes(sender_sk), pk_bytes)], io.BytesIO(plaintext), outfile)

    keys = load_c4gh_keys(str(seckey_path), passphrase)
    result = read_file(fsspec.filesystem("file"), str(c4gh_file), keys=keys)

    assert result == plaintext
