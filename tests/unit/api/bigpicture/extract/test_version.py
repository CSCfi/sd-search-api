import pytest

from search_api.api.bigpicture.extract.version import (
    BigpictureVersion,
    extract_version,
)
from search_api.exceptions import UserException
from search_api.utils.xml import parse_xml


def _dataset_xml(metadata_standard: str | None) -> str:
    """A dataset XML with or without the METADATA_STANDARD tag."""
    version = (
        f"<METADATA_STANDARD>{metadata_standard}</METADATA_STANDARD>"
        if metadata_standard is not None
        else ""
    )
    return f"""
        <DATASET_SET>
            <DATASET alias="1">
                <TITLE>test_title</TITLE>
                {version}
            </DATASET>
        </DATASET_SET>
    """


def _extract_version(metadata_standard: str | None) -> BigpictureVersion:
    return extract_version(parse_xml(_dataset_xml(metadata_standard)))


@pytest.mark.parametrize(
    ("metadata_standard", "expected"),
    [
        ("2.0.0", BigpictureVersion.V2_0),
        ("2.0.11", BigpictureVersion.V2_0),
        ("3.0.0", BigpictureVersion.V3_0),
        (" 3.0.0 ", BigpictureVersion.V3_0),
    ],
)
def test_extract_version(metadata_standard: str, expected: BigpictureVersion):
    assert _extract_version(metadata_standard) == expected


@pytest.mark.parametrize(
    "metadata_standard",
    [
        "1.0.0",  # Before the first supported version.
        "4.0.0",  # After the last supported version.
        "2.1.0",  # A supported major version, but an unknown minor one.
        "3.0",  # Not a <major>.<minor>.<patch> version.
        "3.0.0-beta",
        "three",
    ],
)
def test_extract_version_unsupported(metadata_standard: str):
    with pytest.raises(UserException, match="Unsupported metadata standard version"):
        _extract_version(metadata_standard)


@pytest.mark.parametrize("metadata_standard", [None, ""])
def test_extract_version_missing(metadata_standard: str | None):
    with pytest.raises(UserException, match="Missing 'METADATA_STANDARD'"):
        _extract_version(metadata_standard)
