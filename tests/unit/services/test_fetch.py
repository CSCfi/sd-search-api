"""Tests for the client of the SD submit fetch API."""

import json
from datetime import datetime, timezone

import httpx
import pytest

from search_api.exceptions import SystemException, UserException
from search_api.services.fetch import (
    _SD_SUBMIT_SYNC_PATH,
    SdSubmitFetchClient,
    SdSubmitPublishedSubmission,
)

SD_SUBMIT_API_URL = "https://submitter.example/api"
SD_SUBMIT_API_KEY = "sync_api_key"

SINCE = datetime(2026, 1, 1, tzinfo=timezone.utc)
UNTIL = datetime(2026, 2, 1, tzinfo=timezone.utc)


def _mock_response(payload: object) -> httpx.Response:
    return httpx.Response(
        200, content=json.dumps(payload), headers={"Content-Type": "application/json"}
    )


def _mock_submissions_response(
    *submissions: SdSubmitPublishedSubmission,
) -> httpx.Response:
    """A SD Submit API sync submissions listing."""

    return _mock_response(
        {
            "submissions": [
                {
                    "submissionId": submission.submission_id,
                    "published": submission.published.isoformat(),
                }
                for submission in submissions
            ]
        }
    )


def _mock_sd_submit_client(
    response: httpx.Response,
) -> tuple[SdSubmitFetchClient, list[httpx.Request]]:
    """A client returning the given response, and the requests it made."""

    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return response

    client = SdSubmitFetchClient(
        SD_SUBMIT_API_URL, SD_SUBMIT_API_KEY, transport=httpx.MockTransport(handler)
    )
    return client, requests


@pytest.mark.asyncio
async def test_sd_submit_get_published_submissions() -> None:
    published = [
        SdSubmitPublishedSubmission(submission_id="a", published=SINCE),
        SdSubmitPublishedSubmission(submission_id="b", published=UNTIL),
    ]
    client, requests = _mock_sd_submit_client(_mock_submissions_response(*published))

    async with client:
        submissions = await client.get_published_submissions(SINCE)

    assert submissions == published
    assert requests[0].url.path.endswith(_SD_SUBMIT_SYNC_PATH)
    assert requests[0].headers["authorization"] == f"Bearer {SD_SUBMIT_API_KEY}"


@pytest.mark.parametrize(
    "period,expected_params",
    [
        ((), {}),
        ((SINCE,), {"publishedStart": SINCE.isoformat()}),
        (
            (SINCE, UNTIL),
            {"publishedStart": SINCE.isoformat(), "publishedEnd": UNTIL.isoformat()},
        ),
    ],
    ids=["no period", "since", "since and until"],
)
@pytest.mark.asyncio
async def test_sd_submit_get_published_submissions_period(
    period, expected_params
) -> None:
    client, requests = _mock_sd_submit_client(_mock_submissions_response())

    async with client:
        await client.get_published_submissions(*period)

    assert dict(requests[0].url.params) == expected_params


@pytest.mark.parametrize(
    "published_start,published_end",
    [
        (datetime(2026, 1, 1), None),
        (None, datetime(2026, 2, 1)),
        (SINCE, datetime(2026, 2, 1)),
    ],
)
@pytest.mark.asyncio
async def test_sd_submit_get_published_submissions_no_utc_offset(
    published_start, published_end
) -> None:
    client, requests = _mock_sd_submit_client(_mock_submissions_response())

    async with client:
        with pytest.raises(UserException, match="UTC offset"):
            await client.get_published_submissions(published_start, published_end)

    assert not requests


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"submissions": [{"submissionId": "a"}]},
        {"submissions": [{"submissionId": "a", "published": "not a date"}]},
        {"submissions": "not a list of submissions"},
    ],
)
@pytest.mark.asyncio
async def test_sd_submit_get_published_submissions_invalid_content(
    payload: object,
) -> None:
    client, _ = _mock_sd_submit_client(_mock_response(payload))

    async with client:
        with pytest.raises(SystemException, match=_SD_SUBMIT_SYNC_PATH):
            await client.get_published_submissions(SINCE)


@pytest.mark.parametrize(
    "content_type", ["application/zip", "application/zip; charset=binary"]
)
@pytest.mark.asyncio
async def test_sd_submit_get_submission_objects(content_type: str) -> None:
    client, requests = _mock_sd_submit_client(
        httpx.Response(
            200, content=b"archive bytes", headers={"Content-Type": content_type}
        )
    )

    async with client:
        archive = await client.get_submission_objects("submission_1")

    assert archive == b"archive bytes"
    assert requests[0].url.path.endswith(f"{_SD_SUBMIT_SYNC_PATH}/submission_1")


@pytest.mark.asyncio
async def test_sd_submit_get_submission_objects_invalid_media_type() -> None:
    client, _ = _mock_sd_submit_client(
        httpx.Response(
            200, content=b"archive bytes", headers={"Content-Type": "text/html"}
        )
    )

    async with client:
        with pytest.raises(SystemException, match="instead of 'application/zip'"):
            await client.get_submission_objects("submission_1")


@pytest.mark.asyncio
async def test_sd_submit_client_is_closed() -> None:
    """The SD Submit API client belongs to the context manager."""

    client, _ = _mock_sd_submit_client(_mock_submissions_response())

    with pytest.raises(SystemException, match="outside its context manager"):
        await client.get_published_submissions()

    async with client:
        await client.get_published_submissions()

    with pytest.raises(SystemException, match="outside its context manager"):
        await client.get_published_submissions()
