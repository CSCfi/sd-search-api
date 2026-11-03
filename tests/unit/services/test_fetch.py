"""Tests for the client of the SD submit fetch API."""

import json
from datetime import UTC, datetime
from itertools import count

import httpx
import pytest

from search_api.api.opensearch.models import (
    ExtractedDocument,
    OpenSearchField,
    OpenSearchFieldValue,
)
from search_api.exceptions import SystemException, UserException
from search_api.services.fetch import (
    _SD_SUBMIT_SYNC_PATH,
    SdSubmitFetchClient,
    SdSubmitPublishedSubmission,
    document_set_id,
)

SD_SUBMIT_API_URL = "https://submitter.example/api"
SD_SUBMIT_TOKEN = "sync_token"

SINCE = datetime(2026, 1, 1, tzinfo=UTC)
UNTIL = datetime(2026, 2, 1, tzinfo=UTC)


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

    # Signed per request.
    tokens = count()

    client = SdSubmitFetchClient(
        SD_SUBMIT_API_URL,
        lambda: f"{SD_SUBMIT_TOKEN}-{next(tokens)}",
        transport=httpx.MockTransport(handler),
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
    assert requests[0].headers["authorization"] == f"Bearer {SD_SUBMIT_TOKEN}-0"


@pytest.mark.asyncio
async def test_sd_submit_token_signed_per_request() -> None:
    """A JWT token is created and signed for each request."""

    client, requests = _mock_sd_submit_client(_mock_submissions_response())

    async with client:
        await client.get_published_submissions()
        await client.get_published_submissions()

    assert [request.headers["authorization"] for request in requests] == [
        f"Bearer {SD_SUBMIT_TOKEN}-0",
        f"Bearer {SD_SUBMIT_TOKEN}-1",
    ]


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
        (datetime(2026, 1, 1), None),  # noqa: DTZ001
        (None, datetime(2026, 2, 1)),  # noqa: DTZ001
        (SINCE, datetime(2026, 2, 1)),  # noqa: DTZ001
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


# document_set_id
#

_SET_FIELD = OpenSearchField(id="set_id", type="keyword")


def _extracted_document(doc_id: str, set_id: str) -> ExtractedDocument:
    """A document of the given document set."""
    return ExtractedDocument(
        id=doc_id, values=[OpenSearchFieldValue(field=_SET_FIELD, value=set_id)]
    )


def test_document_set_id_shared_set_id():
    docs = [_extracted_document("id_1", "set_1"), _extracted_document("id_2", "set_1")]
    assert document_set_id(docs, "set_id") == "set_1"


def test_document_set_id_without_documents():
    with pytest.raises(SystemException, match="^A document set has no documents.$"):
        document_set_id([], "set_id")


def test_document_set_id_rejects_several_set_ids():
    docs = [_extracted_document("id_1", "set_1"), _extracted_document("id_2", "set_2")]
    with pytest.raises(
        SystemException,
        match="Documents 'id_1' and 'id_2' belong to different document sets",
    ):
        document_set_id(docs, "set_id")


def test_document_set_id_rejects_document_without_set():
    other_field = OpenSearchField(id="other", type="keyword")
    without_set = ExtractedDocument(
        id="id_2", values=[OpenSearchFieldValue(field=other_field, value="set_1")]
    )
    docs = [_extracted_document("id_1", "set_1"), without_set]
    with pytest.raises(SystemException, match="Document 'id_2' has no document set id"):
        document_set_id(docs, "set_id")


def test_document_set_id_rejects_value_not_a_string():
    integer_field = OpenSearchField(id="set_id", type="integer")
    doc = ExtractedDocument(
        id="id_1", values=[OpenSearchFieldValue(field=integer_field, value=1)]
    )
    with pytest.raises(SystemException, match="not a string: 'set_id' is int"):
        document_set_id([doc], "set_id")
