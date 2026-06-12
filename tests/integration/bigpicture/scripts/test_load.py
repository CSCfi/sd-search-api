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

from scripts.bigpicture.load import main
from search_api.bigpicture.services.load import BigPictureLoadService
from search_api.database.repository import get_connection

os.environ.setdefault("POSTGRES_DB", os.environ["BP_POSTGRES_DB"])
os.environ.setdefault("POSTGRES_PORT", os.environ["BP_POSTGRES_PORT"])

_XML_DIR = (
    Path(__file__).resolve().parent.parent.parent.parent
    / "files"
    / "bigpicture"
    / "xml"
)
_XML_METADATA_FILES = [
    "METADATA/dataset.xml",
    "METADATA/image.xml",
    "METADATA/sample.xml",
    "METADATA/staining.xml",
]
_DATASET_ID = "dataset_1"
_IMAGE_IDS = ["image_1", "image_2"]


@pytest_asyncio.fixture(autouse=True)
async def delete_images():
    """Delete fixture images before and after each test."""

    async def _delete() -> None:
        async with get_connection() as conn:
            async with conn.cursor() as cur:
                for image_id in _IMAGE_IDS:
                    await cur.execute(
                        "DELETE FROM bp_image WHERE image_id = %s", (image_id,)
                    )

    await _delete()
    yield
    await _delete()


@pytest.mark.asyncio
async def test_extract_only():
    with patch.object(
        BigPictureLoadService, "_load_fields", new_callable=AsyncMock
    ) as load_spy:
        await main(
            directory=str(_XML_DIR),
            multi_dir=True,
            load=False,
            sync=False,
            c4gh_private_key_file=None,
            c4gh_passphrase=None,
        )
        load_spy.assert_not_called()

    async with get_connection() as conn:
        async with conn.cursor() as cur:
            for image_id in _IMAGE_IDS:
                fields = await BigPictureLoadService.get_fields(cur, image_id)
                assert fields is None, (
                    f"{image_id!r} was unexpectedly written during dry-run"
                )


@pytest.mark.asyncio
async def test_load_plain_files():
    """The load script processes plain XML files and inserts them into the database."""
    await main(
        directory=str(_XML_DIR),
        multi_dir=True,
        load=True,
        sync=False,
        c4gh_private_key_file=None,
        c4gh_passphrase=None,
    )

    async with get_connection() as conn:
        async with conn.cursor() as cur:
            for image_id in _IMAGE_IDS:
                fields = await BigPictureLoadService.get_fields(cur, image_id)
                assert fields is not None, f"{image_id!r} was not loaded"
                assert fields.image_id == image_id
                assert fields.dataset_id == _DATASET_ID


@pytest.mark.asyncio
async def test_load_c4gh_files(tmp_path):
    """The load script decrypts Crypt4GH-encrypted XML files and inserts them into the database."""
    # Generate a recipient key pair.
    seckey_path = tmp_path / "key.sec"
    pubkey_path = tmp_path / "key.pub"
    c4gh_generate(str(seckey_path), str(pubkey_path), b"", b"")

    recipient_pk = c4gh_get_public_key(str(pubkey_path))
    sender_sk = bytes(PrivateKey.generate())

    # Mirror the fixture directory structure, replacing each XML with a .c4gh version.
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

    await main(
        directory=str(tmp_path),
        multi_dir=True,
        load=True,
        sync=False,
        c4gh_private_key_file=str(seckey_path),
        c4gh_passphrase=None,
    )

    async with get_connection() as conn:
        async with conn.cursor() as cur:
            for image_id in _IMAGE_IDS:
                fields = await BigPictureLoadService.get_fields(cur, image_id)
                assert fields is not None, f"{image_id!r} was not loaded"
                assert fields.image_id == image_id
                assert fields.dataset_id == _DATASET_ID
