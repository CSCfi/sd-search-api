import argparse
import io
import os
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from crypt4gh.keys import get_public_key as c4gh_get_public_key
from crypt4gh.keys.c4gh import generate as c4gh_generate
from crypt4gh.lib import encrypt as c4gh_encrypt
from nacl.public import PrivateKey

from scripts.bigpicture import _load
from search_api.services.load import LoadService
from search_api.database.document import DOCUMENT_TABLE, get_document
from search_api.database.repository import get_connection

os.environ.setdefault("POSTGRES_DB", os.environ["BP_POSTGRES_DB"])
os.environ.setdefault("POSTGRES_PORT", os.environ["BP_POSTGRES_PORT"])

_XML_DIR = (
    Path(__file__).resolve().parent.parent.parent / "files" / "bigpicture" / "xml"
)
_XML_METADATA_FILES = [
    "METADATA/dataset.xml",
    "METADATA/image.xml",
    "METADATA/sample.xml",
    "METADATA/staining.xml",
]
_DATASET_ID = "dataset_1"
_IMAGE_IDS = ["image_1", "image_2"]


def _args(**kwargs) -> argparse.Namespace:
    defaults = dict(
        directory=str(_XML_DIR),
        multi_dir=True,
        load=False,
        sync=False,
        c4gh_key_file=None,
        c4gh_passphrase=None,
    )
    return argparse.Namespace(**{**defaults, **kwargs})


@pytest_asyncio.fixture(autouse=True)
async def delete_images():
    """Delete fixture images before and after each test."""

    async def _delete() -> None:
        async with get_connection() as conn:
            async with conn.cursor() as cur:
                for image_id in _IMAGE_IDS:
                    await cur.execute(
                        f"DELETE FROM {DOCUMENT_TABLE} WHERE id = %s", (image_id,)
                    )

    await _delete()
    yield
    await _delete()


@pytest.mark.asyncio
async def test_load_extract_only():
    with patch.object(
        LoadService, "store_document", new_callable=AsyncMock
    ) as load_spy:
        await _load(_args(load=False))
        load_spy.assert_not_called()

    async with get_connection() as conn:
        async with conn.cursor() as cur:
            for image_id in _IMAGE_IDS:
                payload = await get_document(cur, image_id)
                assert payload is None, (
                    f"{image_id!r} was unexpectedly written during dry-run"
                )


@pytest.mark.asyncio
async def test_load_plain_files():
    """The load command processes plain XML files and inserts them into the database."""
    await _load(_args(load=True))

    async with get_connection() as conn:
        async with conn.cursor() as cur:
            for image_id in _IMAGE_IDS:
                payload = await get_document(cur, image_id)
                assert payload is not None, f"{image_id!r} was not loaded"
                assert payload["image_id"] == image_id
                assert payload["dataset_id"] == _DATASET_ID


@pytest.mark.asyncio
async def test_load_c4gh_files(tmp_path):
    """The load command decrypts Crypt4GH-encrypted XML files and inserts them into the database."""
    seckey_path = tmp_path / "key.sec"
    pubkey_path = tmp_path / "key.pub"
    c4gh_generate(str(seckey_path), str(pubkey_path), b"", b"")

    recipient_pk = c4gh_get_public_key(str(pubkey_path))
    sender_sk = bytes(PrivateKey.generate())

    metadata_dir = tmp_path / _DATASET_ID / "METADATA"
    metadata_dir.mkdir(parents=True)
    for xml_file in _XML_METADATA_FILES:
        src = _XML_DIR / _DATASET_ID / xml_file
        dst = metadata_dir / (Path(xml_file).name + ".c4gh")
        with dst.open("wb") as outfile:
            c4gh_encrypt(
                [(0, sender_sk, recipient_pk)],
                io.BytesIO(src.read_bytes()),
                outfile,
            )

    await _load(
        _args(
            directory=str(tmp_path),
            load=True,
            c4gh_key_file=str(seckey_path),
        )
    )

    async with get_connection() as conn:
        async with conn.cursor() as cur:
            for image_id in _IMAGE_IDS:
                payload = await get_document(cur, image_id)
                assert payload is not None, f"{image_id!r} was not loaded"
                assert payload["image_id"] == image_id
                assert payload["dataset_id"] == _DATASET_ID
