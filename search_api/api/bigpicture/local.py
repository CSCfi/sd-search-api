"""Reading Bigpicture submissions from a directory."""

import logging
from collections.abc import AsyncIterator
from datetime import datetime

import fsspec  # type: ignore

from search_api.api.bigpicture.conf import bigpicture_local_config
from search_api.api.bigpicture.extract.document import (
    dataset_files,
    extract_dataset_documents,
    get_last_modification_time,
)
from search_api.exceptions import SystemException, UserException
from search_api.services.fetch import DocumentSource, SourceDocuments
from search_api.utils.crypt import load_c4gh_keys
from search_api.utils.dir import list_directories

# What makes a directory a dataset directory rather than a parent of them.
_DATASET_FILE = "METADATA/dataset.xml"


def _is_dataset_dir(fs: fsspec.AbstractFileSystem, root: str) -> bool:
    """Return true if the root is one dataset directory rather than a parent of them."""

    path = f"{root}/{_DATASET_FILE}"
    return bool(fs.exists(path) or fs.exists(f"{path}.c4gh"))


def _dataset_modified_at(fs: fsspec.AbstractFileSystem, directory: str) -> datetime:
    """
    When a dataset directory's metadata files were last modified.

    :param fs: The filesystem.
    :param directory: Dataset directory path.
    :raises SystemException: if the filesystem reports no modification time for any file.
    :return: The last modification time.
    """

    modified_at = get_last_modification_time(fs, dataset_files(fs, directory).paths)
    if modified_at is None:
        raise SystemException(
            f"No modification time for any file of dataset {directory}."
        )
    return modified_at


class BigpictureLocalSource(DocumentSource):
    """Reads the Bigpicture datasets from a directory."""

    async def read(
        self,
        root: str | None = None,
        marker: str | None = None,
    ) -> AsyncIterator[SourceDocuments]:
        """Reads the Bigpicture datasets from a directory, oldest modified first.

        :param root: The directory to read. Either one dataset directory or a parent of
            several.
        :param marker: The modification date a previous read reached, in ISO 8601, or None
            for every dataset under the root.
        :raises UserException: if no root was given.
        :return: The documents of each dataset directory.
        """

        if root is None:
            raise UserException("No directory to read was given.")

        modified_since = datetime.fromisoformat(marker) if marker else None

        config = bigpicture_local_config()
        keys = (
            load_c4gh_keys(config.BP_C4GH_KEY_FILE, config.BP_C4GH_PASSPHRASE)
            if config.BP_C4GH_KEY_FILE
            else None
        )

        fs = fsspec.filesystem("file")
        dirs = (
            [root] if _is_dataset_dir(fs, root) else list_directories(root=root, fs=fs)
        )

        # Order datasets by modification date.
        sorted_dirs = sorted(
            ((directory, _dataset_modified_at(fs, directory)) for directory in dirs),
            key=lambda dataset: dataset[1],
        )

        for directory, modified_at in sorted_dirs:
            if modified_since is not None and modified_at <= modified_since:
                logging.info("Skipping dataset %s already loaded.", directory)
                continue

            try:
                documents = list(extract_dataset_documents(directory, fs, keys))
            except Exception:
                logging.error(
                    "Failed to extract documents from dataset %s.",
                    directory,
                    exc_info=True,
                )
                raise

            logging.info(
                "Read dataset %s with %d document(s).", directory, len(documents)
            )
            yield SourceDocuments(marker=modified_at.isoformat(), documents=documents)
