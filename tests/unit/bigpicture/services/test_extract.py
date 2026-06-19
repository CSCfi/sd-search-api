import logging
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from lxml import etree

from search_api.api.opensearch.document import build_document
from search_api.bigpicture.services.extract import (
    BigpictureCodeAttributeValue,
    extract_documents,
    _add_iso8601_durations,
    _extract_anatomical_sites,
    _extract_code_attribute_value,
    _extract_code_attribute_values,
    _extract_fixation_type,
    _extract_string_attribute_value,
    _extract_age_at_extraction_range,
    _get_last_modification_time,
)

TEST_DIR = (
    Path(__file__).resolve().parent.parent.parent.parent
    / "files"
    / "bigpicture"
    / "xml"
)


def test_extract_fields():
    # extract_documents yields ExtractedDocument; build the payload to assert on stored values.
    # (Full code-attribute fidelity — scheme/meaning — is covered by the helper tests below.)
    docs = {doc.id: doc for doc in extract_documents(root=str(TEST_DIR))}
    assert set(docs) == {"image_1", "image_2"}

    payload = build_document(docs["image_1"].values)
    assert payload["image_id"] == "image_1"
    assert payload["dataset_id"] == "dataset_1"
    assert payload["dataset_description"] == "test_description"
    block = payload["blocks"][0]
    assert block["animal_species"] == "1"
    assert block["block_preparation"] == "5"
    assert block["sex"] == "Male"
    assert block["anatomical_site"] == ["2"]
    assert block["fixation_type"] == "3"
    assert block["specimen_type"] == "4"
    assert block["age_at_extraction"] == {"gte": 14600, "lte": 14965}
    stain = payload["stains"][0]
    assert stain["staining_procedure"] == "6"
    assert stain["staining_procedure_other"] == "test6"
    assert "staining_target" not in stain

    payload2 = build_document(docs["image_2"].values)
    stain2 = payload2["stains"][0]
    assert stain2["staining_procedure"] == "7"
    assert stain2["staining_procedure_other"] == "test7"
    assert stain2["staining_target"] == "pan Cytokeratin"


def test_process_code_attribute():
    xml = """
    <ROOT>
        <ATTRIBUTES>
            <CODE_ATTRIBUTE>
                <TAG>animal_species</TAG>
                <VALUE>
                    <CODE>1</CODE>
                    <MEANING>Cat</MEANING>
                </VALUE>
            </CODE_ATTRIBUTE>
            <CODE_ATTRIBUTE>
                <TAG>other</TAG>
                <VALUE>
                    <CODE>2</CODE>
                    <MEANING>Other</MEANING>
                </VALUE>
            </CODE_ATTRIBUTE>
        </ATTRIBUTES>
    </ROOT>
    """
    elem = etree.fromstring(xml)

    attribute = _extract_code_attribute_value(elem, "animal_species")

    assert attribute.code == "1"
    assert attribute.meaning == "Cat"


def test_process_string_attribute():
    xml = """
    <ROOT>
        <ATTRIBUTES>
            <STRING_ATTRIBUTE>
                <TAG>sex</TAG>
                <VALUE>Male</VALUE>
            </STRING_ATTRIBUTE>
        </ATTRIBUTES>
    </ROOT>
    """
    elem = etree.fromstring(xml)

    value = _extract_string_attribute_value(elem, "sex")

    assert value == "Male"


def test_extract_age_at_extraction_range_valid():
    xml = """
    <ATTRIBUTES>
        <SET_ATTRIBUTE>
            <TAG>age_at_extraction</TAG>
            <VALUE>
                <STRING_ATTRIBUTE>
                    <TAG>interval_start</TAG>
                    <VALUE>P40Y</VALUE>
                </STRING_ATTRIBUTE>
                <STRING_ATTRIBUTE>
                    <TAG>interval_length</TAG>
                    <VALUE>P1Y</VALUE>
                </STRING_ATTRIBUTE>
            </VALUE>
        </SET_ATTRIBUTE>
    </ATTRIBUTES>
    """
    elem = etree.fromstring(xml)

    result = _extract_age_at_extraction_range(elem)

    assert result == ("P40Y", "P41Y")


def test_extract_age_at_extraction_range_invalid(caplog):

    xml = """
    <ATTRIBUTES>
        <SET_ATTRIBUTE>
            <TAG>age_at_extraction</TAG>
            <VALUE>
                <STRING_ATTRIBUTE>
                    <TAG>interval_start</TAG>
                    <VALUE>NOT_VALID</VALUE>
                </STRING_ATTRIBUTE>
                <STRING_ATTRIBUTE>
                    <TAG>interval_length</TAG>
                    <VALUE>P1Y</VALUE>
                </STRING_ATTRIBUTE>
            </VALUE>
        </SET_ATTRIBUTE>
    </ATTRIBUTES>
    """
    elem = etree.fromstring(xml)

    with caplog.at_level(logging.ERROR):
        result = _extract_age_at_extraction_range(elem)

    assert result is None
    assert "NOT_VALID" in caplog.text


def test_add_iso8601_durations():
    assert _add_iso8601_durations("P40Y", "P1Y") == "P41Y"
    assert _add_iso8601_durations("P40Y", "PT0S") == "P40Y"
    assert _add_iso8601_durations("P40Y", "P6M") == "P40Y6M"
    assert _add_iso8601_durations("P40Y6M", "P6M") == "P41Y"
    assert _add_iso8601_durations("P1Y", "P11M") == "P1Y11M"
    assert _add_iso8601_durations("P1Y", "P12M") == "P2Y"
    assert _add_iso8601_durations("PT0S", "P1Y") == "P1Y"


def test_process_age_of_extraction_range():
    xml = """
    <ATTRIBUTES>
        <SET_ATTRIBUTE>
            <TAG>age_at_extraction</TAG>
            <VALUE>
                <STRING_ATTRIBUTE>
                <TAG>interval_start</TAG>
                <VALUE>P40Y</VALUE>
                </STRING_ATTRIBUTE>
                <STRING_ATTRIBUTE>
                <TAG>interval_length</TAG>
                <VALUE>P1Y</VALUE>
                </STRING_ATTRIBUTE>
            </VALUE>
        </SET_ATTRIBUTE>
    </ATTRIBUTES>
    """
    elem = etree.fromstring(xml)

    start, end = _extract_age_at_extraction_range(elem)

    assert start == "P40Y"
    assert end == "P41Y"

    # PT0S interval length — end equals start
    xml = """
     <ATTRIBUTES>
         <SET_ATTRIBUTE>
             <TAG>age_at_extraction</TAG>
             <VALUE>
                 <STRING_ATTRIBUTE>
                 <TAG>interval_start</TAG>
                 <VALUE>P40Y</VALUE>
                 </STRING_ATTRIBUTE>
                 <STRING_ATTRIBUTE>
                 <TAG>interval_length</TAG>
                 <VALUE>PT0S</VALUE>
                 </STRING_ATTRIBUTE>
             </VALUE>
         </SET_ATTRIBUTE>
     </ATTRIBUTES>
     """
    elem = etree.fromstring(xml)

    start, end = _extract_age_at_extraction_range(elem)

    assert start == "P40Y"
    assert end == "P40Y"


def test_extract_code_attribute_values_single():
    xml = """
    <ROOT>
        <ATTRIBUTES>
            <CODE_ATTRIBUTE>
                <TAG>anatomical_site</TAG>
                <VALUE>
                    <CODE>2</CODE>
                    <SCHEME>Scheme2</SCHEME>
                    <MEANING>Test2</MEANING>
                    <SCHEME_VERSION/>
                </VALUE>
            </CODE_ATTRIBUTE>
        </ATTRIBUTES>
    </ROOT>
    """
    elem = etree.fromstring(xml)

    values = _extract_code_attribute_values(elem, "anatomical_site")

    assert values == frozenset(
        [
            BigpictureCodeAttributeValue(
                code="2", scheme="Scheme2", meaning="Test2", scheme_version=""
            )
        ]
    )


def test_extract_code_attribute_values_from_list():
    xml = """
    <ROOT>
        <ATTRIBUTES>
            <CODE_ATTRIBUTE>
                <TAG>anatomical_site</TAG>
                <VALUE>
                    <CODE>2</CODE>
                    <SCHEME>Scheme2</SCHEME>
                    <MEANING>Test2</MEANING>
                    <SCHEME_VERSION/>
                </VALUE>
            </CODE_ATTRIBUTE>
            <CODE_ATTRIBUTE>
                <TAG>anatomical_site</TAG>
                <VALUE>
                    <CODE>8</CODE>
                    <SCHEME>Scheme8</SCHEME>
                    <MEANING>Test8</MEANING>
                    <SCHEME_VERSION/>
                </VALUE>
            </CODE_ATTRIBUTE>
        </ATTRIBUTES>
    </ROOT>
    """
    elem = etree.fromstring(xml)

    values = _extract_code_attribute_values(elem, "anatomical_site")

    assert values == frozenset(
        [
            BigpictureCodeAttributeValue(
                code="2", scheme="Scheme2", meaning="Test2", scheme_version=""
            ),
            BigpictureCodeAttributeValue(
                code="8", scheme="Scheme8", meaning="Test8", scheme_version=""
            ),
        ]
    )


def test_extract_anatomical_sites_from_set():
    xml = """
    <ROOT>
        <ATTRIBUTES>
            <SET_ATTRIBUTE>
                <TAG>anatomical_site_list</TAG>
                <VALUE>
                    <CODE_ATTRIBUTE>
                        <TAG>anatomical_site</TAG>
                        <VALUE>
                            <CODE>2</CODE>
                            <SCHEME>Scheme2</SCHEME>
                            <MEANING>Test2</MEANING>
                            <SCHEME_VERSION/>
                        </VALUE>
                    </CODE_ATTRIBUTE>
                    <CODE_ATTRIBUTE>
                        <TAG>anatomical_site</TAG>
                        <VALUE>
                            <CODE>8</CODE>
                            <SCHEME>Scheme8</SCHEME>
                            <MEANING>Test8</MEANING>
                            <SCHEME_VERSION/>
                        </VALUE>
                    </CODE_ATTRIBUTE>
                </VALUE>
            </SET_ATTRIBUTE>
        </ATTRIBUTES>
    </ROOT>
    """
    elem = etree.fromstring(xml)

    sites = _extract_anatomical_sites(elem)

    assert sites == frozenset(
        [
            BigpictureCodeAttributeValue(
                code="2", scheme="Scheme2", meaning="Test2", scheme_version=""
            ),
            BigpictureCodeAttributeValue(
                code="8", scheme="Scheme8", meaning="Test8", scheme_version=""
            ),
        ]
    )


def test_extract_anatomical_sites_from_list_and_set():
    xml = """
    <ROOT>
        <ATTRIBUTES>
            <CODE_ATTRIBUTE>
                <TAG>anatomical_site</TAG>
                <VALUE>
                    <CODE>1</CODE>
                    <SCHEME>Scheme1</SCHEME>
                    <MEANING>Test1</MEANING>
                    <SCHEME_VERSION/>
                </VALUE>
            </CODE_ATTRIBUTE>
            <SET_ATTRIBUTE>
                <TAG>anatomical_site_list</TAG>
                <VALUE>
                    <CODE_ATTRIBUTE>
                        <TAG>anatomical_site</TAG>
                        <VALUE>
                            <CODE>2</CODE>
                            <SCHEME>Scheme2</SCHEME>
                            <MEANING>Test2</MEANING>
                            <SCHEME_VERSION/>
                        </VALUE>
                    </CODE_ATTRIBUTE>
                </VALUE>
            </SET_ATTRIBUTE>
        </ATTRIBUTES>
    </ROOT>
    """
    elem = etree.fromstring(xml)

    sites = _extract_anatomical_sites(elem)

    assert sites == frozenset(
        [
            BigpictureCodeAttributeValue(
                code="1", scheme="Scheme1", meaning="Test1", scheme_version=""
            ),
            BigpictureCodeAttributeValue(
                code="2", scheme="Scheme2", meaning="Test2", scheme_version=""
            ),
        ]
    )


def test_extract_fixation_type_standard_scheme():
    xml = """
    <ROOT>
        <ATTRIBUTES>
            <CODE_ATTRIBUTE>
                <TAG>fixation_type</TAG>
                <VALUE>
                    <CODE>3</CODE>
                    <SCHEME>Scheme3</SCHEME>
                    <MEANING>Test3</MEANING>
                    <SCHEME_VERSION/>
                </VALUE>
            </CODE_ATTRIBUTE>
        </ATTRIBUTES>
    </ROOT>
    """
    elem = etree.fromstring(xml)

    fixation_type, fixation_type_text = _extract_fixation_type(elem)

    assert fixation_type == BigpictureCodeAttributeValue(
        code="3", scheme="Scheme3", meaning="Test3", scheme_version=""
    )
    assert fixation_type_text is None


def test_extract_fixation_type_other_scheme():
    xml = """
    <ROOT>
        <ATTRIBUTES>
            <CODE_ATTRIBUTE>
                <TAG>fixation_type</TAG>
                <VALUE>
                    <CODE>Test7</CODE>
                    <SCHEME>Other</SCHEME>
                    <MEANING>Test7</MEANING>
                    <SCHEME_VERSION/>
                </VALUE>
            </CODE_ATTRIBUTE>
        </ATTRIBUTES>
    </ROOT>
    """
    elem = etree.fromstring(xml)

    fixation_type, fixation_type_text = _extract_fixation_type(elem)

    assert fixation_type is None
    assert fixation_type_text == "Test7"


@pytest.fixture
def mock_fs():
    def _factory(info_map: dict) -> MagicMock:
        fs = MagicMock()
        fs.info.side_effect = lambda path: info_map[path]
        return fs

    return _factory


def test_get_last_modification_time_mtime(mock_fs):
    """mtime as a UNIX timestamp (float) is converted to a UTC datetime."""
    ts = 1_700_000_000.0
    expected = datetime.fromtimestamp(ts, tz=timezone.utc)
    fs = mock_fs({"/a": {"mtime": ts}})

    result = _get_last_modification_time(fs, ["/a"])

    assert result == expected
    assert result.tzinfo == timezone.utc


def test_get_last_modification_time_last_modified(mock_fs):
    """last_modified as a tz-aware datetime is returned unchanged."""
    dt = datetime(2024, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
    fs = mock_fs({"/a": {"last_modified": dt}})

    result = _get_last_modification_time(fs, ["/a"])

    assert result == dt


def test_get_last_modification_time_LastModified(mock_fs):
    """LastModified (S3 key) as a naive datetime gets UTC attached."""
    naive = datetime(2024, 6, 1, 8, 30, 0)
    fs = mock_fs({"/a": {"LastModified": naive}})

    result = _get_last_modification_time(fs, ["/a"])

    assert result == naive.replace(tzinfo=timezone.utc)
    assert result.tzinfo == timezone.utc


def test_get_last_modification_time_returns_max(mock_fs):
    """The newest mtime across multiple paths is returned."""
    older = datetime(2023, 1, 1, tzinfo=timezone.utc)
    newer = datetime(2024, 6, 1, tzinfo=timezone.utc)
    fs = mock_fs({"/a": {"mtime": older}, "/b": {"mtime": newer}})

    result = _get_last_modification_time(fs, ["/a", "/b"])

    assert result == newer


def test_get_last_modification_time_no_times(mock_fs):
    fs = mock_fs({"/a": {"size": 1234}})

    result = _get_last_modification_time(fs, ["/a"])

    assert result is None


def test_get_last_modification_time_no_files():
    fs = MagicMock()

    result = _get_last_modification_time(fs, [])

    assert result is None
    fs.info.assert_not_called()
