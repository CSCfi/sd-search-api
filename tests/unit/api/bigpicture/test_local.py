"""Tests for reading submissions from a directory."""

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import fsspec  # type: ignore
import pytest

from search_api.api.bigpicture import local
from search_api.api.bigpicture.local import BigpictureLocalSource
from search_api.exceptions import UserException
from search_api.services.fetch import DocumentSet
from tests.utils.bigpicture import (
    CLINICAL_2_0_DATASET_DIR,
    CLINICAL_2_0_DATASET_ID,
    NON_CLINICAL_2_0_DATASET_ID,
    VERSION_DIRS,
    XML_DIR_2_0,
)


@pytest.fixture
def unencrypted(monkeypatch):
    """No Crypt4GH key, which is all a directory read is configured by."""

    monkeypatch.delenv("C4GH_KEY_FILE", raising=False)
    monkeypatch.delenv("C4GH_PASSPHRASE", raising=False)


async def _read(root: Path, **kwargs) -> list[DocumentSet]:
    return [
        document_set
        async for document_set in BigpictureLocalSource().read(str(root), **kwargs)
    ]


@pytest.mark.asyncio
async def test_read_bigpicture_dataset_directory(unencrypted) -> None:
    (document_set,) = await _read(CLINICAL_2_0_DATASET_DIR)
    assert document_set.documents
    assert document_set.id == CLINICAL_2_0_DATASET_ID
    assert document_set.modified_at == datetime.fromisoformat(document_set.marker)


@pytest.mark.asyncio
@pytest.mark.parametrize("version_dir", VERSION_DIRS, ids=lambda path: path.name)
async def test_read_bigpicture_dataset_directories(unencrypted, version_dir) -> None:
    document_sets = await _read(version_dir)
    assert len(document_sets) == len(
        [path for path in version_dir.iterdir() if (path / "METADATA").is_dir()]
    )
    assert all(document_set.documents for document_set in document_sets)


@pytest.mark.asyncio
async def test_read_bigpicture_one_set_per_dataset(unencrypted) -> None:
    document_sets = await _read(XML_DIR_2_0)
    assert {document_set.id for document_set in document_sets} == {
        CLINICAL_2_0_DATASET_ID,
        NON_CLINICAL_2_0_DATASET_ID,
    }


@pytest.mark.asyncio
async def test_read_bigpicture_oldest_first(unencrypted) -> None:
    document_sets = await _read(XML_DIR_2_0)
    markers = [document_set.marker for document_set in document_sets]
    assert markers == sorted(markers)


@pytest.mark.asyncio
async def test_read_bigpicture_skips_not_newer(unencrypted) -> None:
    document_sets = await _read(CLINICAL_2_0_DATASET_DIR)
    assert len(document_sets) == 1
    marker = document_sets[0].marker

    # Marker causes datasets to be skipped on subsequent read.
    assert await _read(CLINICAL_2_0_DATASET_DIR, marker=marker) == []

    earlier = datetime.fromisoformat(marker) - timedelta(seconds=1)
    assert (
        await _read(CLINICAL_2_0_DATASET_DIR, marker=earlier.isoformat())
        == document_sets
    )


@pytest.mark.asyncio
async def test_read_bigpicture_missing_directory(unencrypted) -> None:
    with pytest.raises(UserException, match="No directory to read"):
        _ = [document_set async for document_set in BigpictureLocalSource().read()]


@pytest.mark.asyncio
async def test_read_bigpicture_missing_key_file(unencrypted, monkeypatch) -> None:
    monkeypatch.setenv("C4GH_KEY_FILE", "/nonexistent.sec")

    with pytest.raises(Exception) as raised:
        await _read(CLINICAL_2_0_DATASET_DIR)

    assert "nonexistent.sec" in str(raised.value) or isinstance(raised.value, OSError)
    assert not os.path.exists("/nonexistent.sec")


def test_bigpicture_dataset_modified_at() -> None:
    """A Bigpicture dataset directory is dated by the newest of its metadata files."""

    fs = fsspec.filesystem("file")

    modified_at = local._dataset_modified_at(fs, str(CLINICAL_2_0_DATASET_DIR))

    newest = max(
        path.stat().st_mtime
        for path in (CLINICAL_2_0_DATASET_DIR / "METADATA").glob("*.xml")
    )
    assert modified_at == datetime.fromtimestamp(newest, tz=UTC)
