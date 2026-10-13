"""Bigpicture metadata standard versions."""

import re
from enum import StrEnum
from pathlib import Path

from lxml.etree import _ElementTree as ElementTree  # noqa

from search_api.exceptions import UserException
from search_api.utils.xml import get_xml_value


class BigpictureVersion(StrEnum):
    """Supported Bigpicture metadata standard versions.

    Named after the schema directories in the repository.
    """

    V2_0 = "2.0"
    V3_0 = "3.0"


XML_SCHEMA_DIR = Path(__file__).resolve().parent.parent / "schemas"


def xml_schema_dir(version: BigpictureVersion) -> Path:
    """Get the XML schema directory of a metadata standard version."""
    return XML_SCHEMA_DIR / version


# The dataset XML schema restricts METADATA_STANDARD to "<major>.<minor>.<patch>".
_METADATA_STANDARD_PATTERN = re.compile(r"(\d+\.\d+)\.\d+")


def extract_version(dataset_xml: ElementTree) -> BigpictureVersion:
    """Extract the metadata standard version from dataset XML.

    :param dataset_xml: The dataset XML element tree.
    :raises UserException: if the version is missing or is not supported.
    """
    value = get_xml_value(
        "/DATASET/METADATA_STANDARD | /DATASET_SET/DATASET/METADATA_STANDARD",
        dataset_xml,
        optional=True,
    )
    if value is None:
        raise UserException("Missing 'METADATA_STANDARD' element.")

    match = _METADATA_STANDARD_PATTERN.fullmatch(value.strip())
    if match is None or match.group(1) not in BigpictureVersion:
        supported = ", ".join(f"{version}.x" for version in BigpictureVersion)
        raise UserException(
            f"Unsupported metadata standard version {value!r}. Supported: {supported}."
        )
    return BigpictureVersion(match.group(1))
