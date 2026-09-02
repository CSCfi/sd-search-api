"""Tests for reading submissions from a directory."""

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import fsspec  # type: ignore
import pytest

from search_api.api.bigpicture import local
from search_api.api.bigpicture.local import BigpictureLocalSource
from search_api.exceptions import UserException
from search_api.services.fetch import SourceDocuments

FILES_DIR = Path(__file__).parent.parent.parent.parent / "files" / "bigpicture" / "xml"
DATASET_DIR = FILES_DIR / "dataset_clinical"


@pytest.fixture
def unencrypted(monkeypatch):
    """No Crypt4GH key, which is all a directory read is configured by."""

    monkeypatch.delenv("BP_C4GH_KEY_FILE", raising=False)
    monkeypatch.delenv("BP_C4GH_PASSPHRASE", raising=False)


async def _read(root: Path, **kwargs) -> list[SourceDocuments]:
    return [docs async for docs in BigpictureLocalSource().read(str(root), **kwargs)]


@pytest.mark.asyncio
async def test_read_bigpicture_dataset_directory(unencrypted) -> None:
    source_docs = await _read(DATASET_DIR)
    assert len(source_docs) == 1
    assert source_docs[0].documents
    assert datetime.fromisoformat(source_docs[0].marker)


@pytest.mark.asyncio
async def test_read_bigpicture_dataset_directories(unencrypted) -> None:
    source_docs = await _read(FILES_DIR)
    assert len(source_docs) == len(
        [path for path in FILES_DIR.iterdir() if (path / "METADATA").is_dir()]
    )
    assert all(unit.documents for unit in source_docs)


@pytest.mark.asyncio
async def test_read_bigpicture_oldest_first(unencrypted) -> None:
    source_docs = await _read(FILES_DIR)
    markers = [unit.marker for unit in source_docs]
    assert markers == sorted(markers)


@pytest.mark.asyncio
async def test_read_bigpicture_skips_not_newer(unencrypted) -> None:
    source_docs = await _read(DATASET_DIR)
    assert len(source_docs) == 1
    marker = source_docs[0].marker

    # Marker causes datasets to be skipped on subsequent read.
    assert await _read(DATASET_DIR, marker=marker) == []

    earlier = datetime.fromisoformat(marker) - timedelta(seconds=1)
    assert await _read(DATASET_DIR, marker=earlier.isoformat()) == source_docs


@pytest.mark.asyncio
async def test_read_bigpicture_missing_directory(unencrypted) -> None:
    with pytest.raises(UserException, match="No directory to read"):
        _ = [docs async for docs in BigpictureLocalSource().read()]


@pytest.mark.asyncio
async def test_read_bigpicture_missing_key_file(unencrypted, monkeypatch) -> None:
    monkeypatch.setenv("BP_C4GH_KEY_FILE", "/nonexistent.sec")

    with pytest.raises(Exception) as raised:
        await _read(DATASET_DIR)

    assert "nonexistent.sec" in str(raised.value) or isinstance(raised.value, OSError)
    assert not os.path.exists("/nonexistent.sec")


def test_bigpicture_dataset_modified_at() -> None:
    """A Bigpicture dataset directory is dated by the newest of its metadata files."""

    fs = fsspec.filesystem("file")

    modified_at = local._dataset_modified_at(fs, str(DATASET_DIR))

    newest = max(
        path.stat().st_mtime for path in (DATASET_DIR / "METADATA").glob("*.xml")
    )
    assert modified_at == datetime.fromtimestamp(newest, tz=timezone.utc)
