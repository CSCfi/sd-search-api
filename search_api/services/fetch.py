"""Fetch documents from a deployment's remote source.

Includes an implementation that uses the SD Submit API sync API.
"""

import logging
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import datetime
from types import TracebackType

import httpx

from search_api.api.opensearch.models import ExtractedDocument
from search_api.exceptions import SystemException, UserException


@dataclass(frozen=True)
class SourceDocuments:
    """Documents read from a source."""

    # How far the source has been read once these are stored, as a marker only the source
    # understands: it is what a later read is given to resume from.
    marker: str
    documents: list[ExtractedDocument] = field(default_factory=list)


class DocumentSource(ABC):
    """The source for a deployment's documents."""

    @abstractmethod
    def read(
        self,
        root: str | None = None,
        marker: str | None = None,
    ) -> AsyncIterator[SourceDocuments]:
        """
        Get the documents modified since the marker.

        :param root: The directory root to read from.
        :param marker: The incremental load position.
        :return: The source documents to index.
        """


def _validate_utc_offset(name: str, value: datetime | None) -> None:
    """
    Require a publication date to specify a UTC offset.

    The submitter rejects a date without one, since it would be resolved in the timezone of
    its database rather than in UTC and would shift the period asked for.

    :param name: The argument name.
    :param value: The publication date, or None when it was not given.
    :raises UserException: if the publication date does not specify a UTC offset.
    """

    if value is not None and value.utcoffset() is None:
        raise UserException(f"The '{name}' date must specify a UTC offset.")


_SD_SUBMIT_SYNC_PATH = "/sync/submissions"
_SD_SUBMIT_TIMEOUT = 300.0
_SD_SUBMIT_ARCHIVE_MEDIA_TYPE = "application/zip"


@dataclass(frozen=True)
class SdSubmitPublishedSubmission:
    submission_id: str
    published: datetime


class SdSubmitFetchClient:
    """Fetches published submissions from the SD submit API."""

    def __init__(
        self,
        url: str,
        api_key: str,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        """
        Fetches published submissions from the SD submit API.

        :param url: The SD submit API base URL.
        :param api_key: The bearer token of the sync service account.
        :param transport: The HTTP transport, for answering the requests in tests.
        """
        self._url = url.rstrip("/")
        self._api_key = api_key
        self._transport = transport
        # Managed by the context manager.
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "SdSubmitFetchClient":
        """Open the HTTP client."""
        self._client = httpx.AsyncClient(
            headers={"Authorization": f"Bearer {self._api_key}"},
            timeout=_SD_SUBMIT_TIMEOUT,
            transport=self._transport,
        )
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Close the HTTP client."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def get_published_submissions(
        self,
        published_start: datetime | None = None,
        published_end: datetime | None = None,
    ) -> list[SdSubmitPublishedSubmission]:
        """
        Get the submissions published within the given period, most recently published last.

        :param published_start: The first publication date to return, inclusive, or None for
            everything published up to published_end.
        :param published_end: The last publication date to return, inclusive, or None for
            everything published since published_start.
        :raises UserException: if a publication date does not specify a UTC offset.
        :raises SystemException: if the submitter does not answer with the submissions.
        :return: The published submissions, most recently published last.
        """

        _validate_utc_offset("published_start", published_start)
        _validate_utc_offset("published_end", published_end)

        params = {}
        if published_start is not None:
            params["publishedStart"] = published_start.isoformat()
        if published_end is not None:
            params["publishedEnd"] = published_end.isoformat()

        path = _SD_SUBMIT_SYNC_PATH
        try:
            response = await self._request("GET", path, params=params)
            submissions = response.json()["submissions"]
            return [
                SdSubmitPublishedSubmission(
                    submission_id=submission["submissionId"],
                    published=datetime.fromisoformat(submission["published"]),
                )
                for submission in submissions
            ]
        except SystemException:
            raise
        except Exception as ex:
            raise SystemException(f"SD submit API '{path}' error.") from ex

    async def get_submission_objects(self, submission_id: str) -> bytes:
        """
        Get the metadata objects of one published submission.

        :param submission_id: The submission id.
        :raises SystemException: if the submitter does not answer with the metadata objects.
        :return: The zip archive of the metadata objects.
        """

        path = f"{_SD_SUBMIT_SYNC_PATH}/{submission_id}"
        response = await self._request("GET", path)

        media_type = response.headers.get("Content-Type", "").split(";")[0].strip()
        if media_type != _SD_SUBMIT_ARCHIVE_MEDIA_TYPE:
            raise SystemException(
                f"SD submit API '{path}' invalid media type '{media_type}' "
                f"instead of '{_SD_SUBMIT_ARCHIVE_MEDIA_TYPE}'."
            )

        return response.content

    async def _request(
        self, method: str, path: str, params: dict[str, str] | None = None
    ) -> httpx.Response:
        """
        Make one request to the SD submit API.

        :param method: The HTTP method.
        :param path: The path, relative to the submitter base URL.
        :param params: The query parameters.
        :raises SystemException: if the client is not open or the request fails.
        :return: The response.
        """

        if self._client is None:
            raise SystemException(
                "The SD submit fetch client is used outside its context manager."
            )

        url = f"{self._url}{path}"
        try:
            response = await self._client.request(method, url, params=params)
        except httpx.HTTPError as ex:
            raise SystemException(f"SD submit API request to '{url}' failed.") from ex

        if response.is_error:
            logging.error(
                "SD submit API %s request to '%s' returned %d: %s",
                method,
                url,
                response.status_code,
                response.text,
            )
            raise SystemException(
                f"SD submit API answered '{url}' with {response.status_code}."
            )

        return response
