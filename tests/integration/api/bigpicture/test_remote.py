"""Tests for fetching Bigpicture submissions from a SD submit API."""

from datetime import timedelta

import pytest
import pytest_asyncio

from search_api.api.bigpicture.conf import bigpicture_remote_config
from search_api.api.bigpicture.remote import BigpictureRemoteSource, _extract_archive
from search_api.exceptions import SystemException
from search_api.services.fetch import SdSubmitFetchClient, SdSubmitPublishedSubmission

pytestmark = pytest.mark.requires_submit


@pytest_asyncio.fixture
async def sd_submit_api():
    config = bigpicture_remote_config()
    async with SdSubmitFetchClient(
        config.BP_SUBMIT_API_URL, config.BP_SUBMIT_API_KEY
    ) as client:
        yield client


@pytest_asyncio.fixture
async def published_submissions(sd_submit_api) -> list[SdSubmitPublishedSubmission]:
    """Return published submissions, or skip when they do not exist."""

    submissions = await sd_submit_api.get_published_submissions()
    if not submissions:
        pytest.skip("The Bigpicture submit API has published no submissions")
    return submissions


@pytest.mark.asyncio
async def test_get_published_submissions(published_submissions):
    assert all(submission.submission_id for submission in published_submissions)
    assert all(
        submission.published.utcoffset() is not None
        for submission in published_submissions
    )


@pytest.mark.asyncio
async def test_get_published_submissions_oldest_first(published_submissions):
    dates = [submission.published for submission in published_submissions]
    assert dates == sorted(dates)


@pytest.mark.asyncio
async def test_get_published_submissions_incremental(
    sd_submit_api, published_submissions
):
    submissions = published_submissions
    newest = submissions[-1].published

    assert (
        await sd_submit_api.get_published_submissions(newest + timedelta(seconds=1))
        == []
    )

    since_newest = await sd_submit_api.get_published_submissions(newest)
    assert submissions[-1].submission_id in [
        submission.submission_id for submission in since_newest
    ]
    if len(submissions) > 1:
        assert len(since_newest) < len(submissions)


@pytest.mark.asyncio
async def test_get_submission_objects(sd_submit_api, published_submissions):
    archive = await sd_submit_api.get_submission_objects(
        published_submissions[-1].submission_id
    )
    documents = list(_extract_archive(archive))

    assert documents
    assert all(document.id for document in documents)


@pytest.mark.asyncio
async def test_invalid_api_key():
    config = bigpicture_remote_config()
    async with SdSubmitFetchClient(
        config.BP_SUBMIT_API_URL, "not the sync api key"
    ) as client:
        with pytest.raises(SystemException, match="401"):
            await client.get_published_submissions()


@pytest.mark.asyncio
async def test_read_source(published_submissions):
    source_docs = [source async for source in BigpictureRemoteSource().read()]

    assert source_docs
    assert all(unit.documents for unit in source_docs)
    assert [unit.marker for unit in source_docs] == sorted(
        unit.marker for unit in source_docs
    )
