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

CLINICAL_DATASET_DIR = (
    Path(__file__).parent.parent.parent.parent
    / "files"
    / "bigpicture"
    / "xml"
    / "dataset_clinical"
)

SUBMISSION_ID = "submission_1"
PUBLISHED = datetime(2026, 1, 2, 12, 0, tzinfo=timezone.utc)


def _clinical_dataset_archive() -> bytes:
    """Clinical test dataset in the SD Submit API archive format."""

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for path in sorted((CLINICAL_DATASET_DIR / "METADATA").glob("*.xml")):
            archive.writestr(f"METADATA/{path.name}", path.read_text(encoding="utf-8"))
    return buffer.getvalue()


def test_extract_archive() -> None:
    """Compare clinical test dataset against its SD Submit API archive."""

    from_archive = list(_extract_archive(_clinical_dataset_archive()))
    from_directory = list(extract_dataset_documents(str(CLINICAL_DATASET_DIR)))

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
        return _clinical_dataset_archive()

    monkeypatch.setenv("BP_SUBMIT_API_URL", "test")
    monkeypatch.setenv("BP_SUBMIT_API_KEY", "test")
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
