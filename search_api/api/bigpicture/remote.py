"""Fetching published submissions from the Bigpicture submit API."""

import io
import logging
import zipfile
from collections.abc import AsyncIterator, Iterator
from datetime import datetime, timedelta
from typing import override

from fsspec.implementations.zip import ZipFileSystem  # type: ignore

from search_api.api.bigpicture.conf import bigpicture_remote_config
from search_api.api.bigpicture.extract.document import extract_dataset_documents
from search_api.api.opensearch.models import ExtractedDocument
from search_api.exceptions import UserException
from search_api.services.fetch import DocumentSource, SourceDocuments
from search_api.services.fetch import SdSubmitFetchClient


def _extract_archive(archive: bytes) -> Iterator[ExtractedDocument]:
    """
    Read the documents of one submission archive fetched from the Bigpicture submit API.

    The Bigpicture submit API returns a submission as a zip archive of
    the submitted and accessioned XML documents, using the file and
    directory names from the Bigpicture submission preparation guide.

    :param archive: The zip archive of one submission.
    :raises UserException: if the archive is not a readable zip archive.
    :return: The extracted documents.
    """

    try:
        fs = ZipFileSystem(io.BytesIO(archive))
    except zipfile.BadZipFile as ex:
        raise UserException(
            "The submission archive is not a readable zip archive."
        ) from ex

    return extract_dataset_documents("/", fs)


# Account for transactional loads started before and finished after previous fetch.
_FETCH_OVERLAP = timedelta(minutes=10)


class BigpictureRemoteSource(DocumentSource):
    """Fetches the submissions published by the Bigpicture submit API."""

    @override
    async def read(
        self,
        root: str | None = None,
        marker: str | None = None,
    ) -> AsyncIterator[SourceDocuments]:
        """
        Fetch the submissions published since the marker, oldest published first.

        :param root: Ignored parameter.
        :param marker: The publication date a previous fetch reached, in ISO 8601, or None
            for every published submission.
        :return: The documents of each published submission.
        """

        published_since = (
            datetime.fromisoformat(marker) - _FETCH_OVERLAP if marker else None
        )

        config = bigpicture_remote_config()
        async with SdSubmitFetchClient(
            config.BP_SUBMIT_API_URL, config.BP_SUBMIT_API_KEY
        ) as client:
            submissions = await client.get_published_submissions(published_since)
            logging.info(
                "%d submission(s) published %s.",
                len(submissions),
                f"since {published_since.isoformat()}"
                if published_since
                else "in total",
            )

            for submission in submissions:
                archive = await client.get_submission_objects(submission.submission_id)

                documents = [
                    document.model_copy(update={"modified_at": submission.published})
                    for document in _extract_archive(archive)
                ]

                logging.info(
                    "Fetched submission %s with %d document(s).",
                    submission.submission_id,
                    len(documents),
                )

                yield SourceDocuments(
                    marker=submission.published.isoformat(),
                    documents=documents,
                )
