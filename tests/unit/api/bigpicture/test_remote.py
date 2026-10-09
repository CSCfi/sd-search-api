"""Tests for fetching published submissions from the Bigpicture submit API."""

import io
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from search_api.api.bigpicture.extract.document import extract_dataset_documents
from search_api.api.bigpicture.remote import BigpictureRemoteSource, _extract_archive
from search_api.exceptions import UserException
from search_api.services.fetch import SdSubmitFetchClient, SdSubmitPublishedSubmission
from tests.utils.bigpicture import (
    CLINICAL_2_0_DATASET_DIR,
    CLINICAL_3_0_DATASET_DIR,
)
from tests.utils.keys import base64_pem_private_key

SUBMISSION_ID = "submission_1"
PUBLISHED = datetime(2026, 1, 2, 12, 0, tzinfo=timezone.utc)


def _dataset_archive(dataset_dir: Path) -> bytes:
    """A test dataset in the SD Submit API archive format."""

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for path in sorted((dataset_dir / "METADATA").glob("*.xml")):
            archive.writestr(f"METADATA/{path.name}", path.read_text(encoding="utf-8"))
    return buffer.getvalue()


@pytest.mark.parametrize(
    "dataset_dir",
    [CLINICAL_2_0_DATASET_DIR, CLINICAL_3_0_DATASET_DIR],
    ids=lambda path: path.parent.name,
)
def test_extract_archive(dataset_dir: Path) -> None:
    """An archive of a dataset reads like its directory."""

    from_archive = list(_extract_archive(_dataset_archive(dataset_dir)))
    from_directory = list(extract_dataset_documents(str(dataset_dir)))

    assert from_archive
    assert from_archive == [
        document.model_copy(update={"modified_at": None}) for document in from_directory
    ]
    assert all(document.modified_at is not None for document in from_directory)


def test_extract_archive_not_zip() -> None:
    with pytest.raises(UserException, match="not a readable zip archive"):
        list(_extract_archive(b"<html>gateway</html>"))


# Fetching.
#


@pytest.fixture
def mock_sd_submit_api(monkeypatch):
    """Returns clinical test dataset."""

    async def get_published_submissions(self, published_start=None, published_end=None):
        return [
            SdSubmitPublishedSubmission(
                submission_id=SUBMISSION_ID, published=PUBLISHED
            )
        ]

    async def get_submission_objects(self, submission_id):
        assert submission_id == SUBMISSION_ID
        return _dataset_archive(CLINICAL_2_0_DATASET_DIR)

    monkeypatch.setenv("SD_SUBMIT_API_URL", "test")
    monkeypatch.setenv("SD_SUBMIT_PRIVATE_KEY", base64_pem_private_key())
    monkeypatch.setenv("SD_SUBMIT_AUDIENCE", "test")
    monkeypatch.setattr(
        SdSubmitFetchClient, "get_published_submissions", get_published_submissions
    )
    monkeypatch.setattr(
        SdSubmitFetchClient, "get_submission_objects", get_submission_objects
    )


@pytest.mark.asyncio
async def test_read_source(
    mock_sd_submit_api,
) -> None:
    source_docs = [source async for source in BigpictureRemoteSource().read()]

    assert [source.marker for source in source_docs] == [PUBLISHED.isoformat()]

    # The clinical dataset has two images (documents).
    documents = source_docs[0].documents
    assert len(documents) == 2
    assert all(document.modified_at == PUBLISHED for document in documents)
