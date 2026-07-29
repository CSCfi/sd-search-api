"""Crypt4GH file decryption utilities."""

import io
from typing import Literal, overload

import fsspec  # type: ignore
from crypt4gh.keys import get_private_key  # type: ignore[import-untyped]
from crypt4gh.lib import decrypt  # type: ignore[import-untyped]


def load_c4gh_keys(key_file: str, passphrase: str | None = None) -> list:
    """Load a Crypt4GH private key file and return keys for ``crypt4gh.lib.decrypt``.

    :param key_file: Path to the Crypt4GH private key file (.sec).
    :param passphrase: Passphrase protecting the key, or None for an unprotected key.
    """
    callback = (lambda: passphrase) if passphrase else (lambda: "")
    private_key = get_private_key(key_file, callback)
    return [(0, private_key, None)]


@overload
def resolve_path(
    fs: fsspec.AbstractFileSystem, path: str, *, optional: Literal[False] = False
) -> str: ...


@overload
def resolve_path(
    fs: fsspec.AbstractFileSystem, path: str, *, optional: Literal[True]
) -> str | None: ...


def resolve_path(
    fs: fsspec.AbstractFileSystem, path: str, *, optional: bool = False
) -> str | None:
    """Return the resolved path, accepting either a plain file or a ``.c4gh`` variant.

    Tries ``path`` first; falls back to ``path + ".c4gh"``. Raises ``ValueError`` if
    neither exists, unless ``optional`` is True, in which case None is returned.
    """
    if fs.exists(path):
        return path
    c4gh_path = path + ".c4gh"
    if fs.exists(c4gh_path):
        return c4gh_path
    if optional:
        return None
    raise ValueError(f"Missing file: {path}")


def read_file(
    fs: fsspec.AbstractFileSystem,
    path: str,
    keys: list | None,
) -> bytes:
    """Read a file, decrypting it with Crypt4GH if the path ends in ``.c4gh``.

    :param fs: fsspec filesystem.
    :param path: Path to the file (plain or .c4gh).
    :param keys: Crypt4GH decryption keys returned by ``load_c4gh_keys``, or None.
    :raises ValueError: If a .c4gh file is found but no decryption key was provided.
    """
    if path.endswith(".c4gh"):
        if not keys:
            raise ValueError(
                f"Found encrypted file {path} but no decryption key was provided."
            )
        with fs.open(path, "rb") as infile:
            outfile = io.BytesIO()
            decrypt(keys, infile, outfile)
            return outfile.getvalue()
    with fs.open(path, "rb") as f:
        return f.read()
