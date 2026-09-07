"""Tests for fetching Bigpicture submissions from a SD submit API."""

from datetime import timedelta
from functools import partial

import httpx
import pytest
import pytest_asyncio

from search_api.api.bigpicture.conf import (
    BigpictureRemoteConfiguration,
    bigpicture_remote_config,
)
from search_api.api.bigpicture.remote import BigpictureRemoteSource, _extract_archive
from search_api.exceptions import SystemException
from search_api.services.fetch import (
    _SD_SUBMIT_SYNC_PATH,
    SdSubmitFetchClient,
    SdSubmitPublishedSubmission,
)
from search_api.utils.token import sign_service_token
from tests.utils.keys import pem_key_pair

pytestmark = pytest.mark.requires_submit


def _token(config: BigpictureRemoteConfiguration, **overrides: str):
    """
    Sign the JWT service token of one client.

    :param config: The configuration the token is signed from.
    :param overrides: What to sign instead of the configured key, issuer or audience.
    :return: The callable signing one token per request.
    """

    signed = {
        "private_key": config.BP_SUBMIT_PRIVATE_KEY,
        "issuer": config.BP_SUBMIT_ISSUER,
        "audience": config.BP_SUBMIT_AUDIENCE,
    }
    return partial(sign_service_token, **(signed | overrides))


@pytest_asyncio.fixture
async def sd_submit_api():
    config = bigpicture_remote_config()
    async with SdSubmitFetchClient(config.BP_SUBMIT_API_URL, _token(config)) as client:
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


def test_no_token():
    config = bigpicture_remote_config()
    url = f"{config.BP_SUBMIT_API_URL.rstrip('/')}{_SD_SUBMIT_SYNC_PATH}"

    assert httpx.get(url).status_code == 401


@pytest.mark.parametrize(
    "overrides",
    [
        {"private_key": pem_key_pair()[0]},
        {"audience": "https://elsewhere.example/api/sync"},
        {"issuer": "someone-else"},
    ],
    ids=[
        "signed by another key",
        "issued for another audience",
        "issued by another issuer",
    ],
)
@pytest.mark.asyncio
async def test_token_rejected(overrides):
    config = bigpicture_remote_config()

    async with SdSubmitFetchClient(
        config.BP_SUBMIT_API_URL, _token(config, **overrides)
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
