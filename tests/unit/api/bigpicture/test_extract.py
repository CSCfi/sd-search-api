import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from lxml import etree

from search_api.api.opensearch.document import build_document
from search_api.api.beacon.models import SNOMED_ONTOLOGY_ID
from search_api.api.bigpicture.extract import (
    BigpictureCodeAttributeValue,
    ObjectKey,
    ObjectIds,
    extract_dataset_documents,
    _add_iso8601_durations,
    _object_keys,
    _extract_anatomical_sites,
    _extract_code_attribute_value,
    _extract_code_attribute_values,
    _extract_fixation_type,
    _extract_string_attribute_value,
    _extract_age_at_extraction_range,
    _get_last_modification_time,
    _matches_scheme,
    _require_scheme,
    _filter_by_scheme,
)

DATASET_1_DIR = (
    Path(__file__).resolve().parent.parent.parent.parent
    / "files"
    / "bigpicture"
    / "xml"
    / "dataset_1"
)


def _code(scheme: str | None) -> BigpictureCodeAttributeValue:
    return BigpictureCodeAttributeValue(code="1", scheme=scheme, meaning="Test")


def test_extract_fields():
    # extract_dataset_documents yields ExtractedDocument; build the payload to assert on stored values.
    # (Full code-attribute fidelity — scheme/meaning — is covered by the helper tests below.)
    docs = {doc.id: doc for doc in extract_dataset_documents(str(DATASET_1_DIR))}
    assert set(docs) == {"image_1", "image_2"}

    payload = build_document(docs["image_1"].values)
    assert payload["image_id"] == "image_1"
    assert payload["dataset_id"] == "dataset_1"
    assert payload["dataset_description"] == "test_description"

    # The block and biological being are flattened to specimen.
    specimen = payload["specimen"][0]
    assert specimen["block_preparation"] == "5"
    assert specimen["anatomical_site"] == ["2"]
    assert specimen["fixation_type"] == "3"
    assert specimen["specimen_type"] == "4"
    assert specimen["age_at_extraction"] == {"gte": 14600, "lte": 14965}
    assert specimen["animal_species"] == "1"
    assert specimen["sex"] == "Male"

    stain = payload["staining"][0]
    assert stain["staining_procedure"] == "6"
    assert stain["staining_procedure_other"] == "test6"
    assert "staining_target" not in stain

    payload2 = build_document(docs["image_2"].values)
    stain2 = payload2["staining"][0]
    assert stain2["staining_procedure"] == "7"
    assert stain2["staining_procedure_other"] == "test7"
    assert stain2["staining_target"] == "pan Cytokeratin"


def test_extract_diagnoses():
    docs = {doc.id: doc for doc in extract_dataset_documents(str(DATASET_1_DIR))}

    payload1 = build_document(docs["image_1"].values)
    payload2 = build_document(docs["image_2"].values)

    # CASE_REF (Distinct, both images)
    # SPECIMEN_REF (Distinct, both images)
    # IMAGE_REF (Distinct, image_1)
    assert sorted(payload1["diagnosis"]) == ["109355002", "254837009", "73211009"]
    # CASE_REF (Distinct, reaches both)
    # SPECIMEN_REF (Distinct, reaches both)
    # SLIDE_REF (Distinct, image_2 only).
    assert sorted(payload2["diagnosis"]) == ["195967001", "254837009", "73211009"]

    # BIOLOGICAL_BEING_REF (Summary, both images)
    # BLOCK_REF (Summary, both images)
    assert sorted(payload1["diagnosis_candidate"]) == ["363346000", "38341003"]
    assert sorted(payload2["diagnosis_candidate"]) == ["363346000", "38341003"]

    for payload in (payload1, payload2):
        # non-SNOMED code is ignored.
        assert "8500/3" not in payload["diagnosis"]
        assert "8500/3" not in payload["diagnosis_candidate"]
        # Finding statement is ignored.
        assert "404684003" not in payload["diagnosis"]
        assert "404684003" not in payload["diagnosis_candidate"]


def test_object_ids_id():
    """The id is the accession when present, otherwise the mandatory alias."""
    assert ObjectIds(alias="1", accession="slide_1").id == "slide_1"
    assert ObjectIds(alias="1").id == "1"


def test_object_ids_keys():
    """Both the accession and the alias are keys, tagged by kind so an
    accession is never confused with an alias."""
    assert ObjectIds(alias="1", accession="slide_1").keys == [
        ObjectKey(kind="alias", value="1"),
        ObjectKey(kind="accession", value="slide_1"),
    ]
    assert ObjectIds(alias="1").keys == [ObjectKey(kind="alias", value="1")]


def test_object_keys_from_element():
    assert _object_keys(etree.fromstring('<SLIDE alias="1" accession="slide_1"/>')) == [
        ObjectKey(kind="accession", value="slide_1"),
        ObjectKey(kind="alias", value="1"),
    ]
    assert _object_keys(etree.fromstring('<SLIDE alias="1"/>')) == [
        ObjectKey(kind="alias", value="1"),
    ]
    assert _object_keys(etree.fromstring('<SLIDE accession="slide_1"/>')) == [
        ObjectKey(kind="accession", value="slide_1"),
    ]


def test_object_keys_from_object_ids():
    objects = [ObjectIds(alias="1", accession="slide_1"), ObjectIds(alias="2")]
    assert _object_keys(objects) == [
        ObjectKey(kind="alias", value="1"),
        ObjectKey(kind="accession", value="slide_1"),
        ObjectKey(kind="alias", value="2"),
    ]


def _copy_xml_dir(tmp_path: Path) -> Path:
    """Copy the dataset_1 fixture to tmp_path."""
    dst = tmp_path / "dataset_1"
    shutil.copytree(DATASET_1_DIR, dst)
    return dst


def _replace_in_xml(path: Path, old: str, new: str) -> None:
    path.write_text(path.read_text().replace(old, new))


def test_extract_requires_dataset_accession(tmp_path):
    root = _copy_xml_dir(tmp_path)
    _replace_in_xml(
        root / "METADATA" / "dataset.xml",
        '<DATASET alias="1" accession="dataset_1">',
        '<DATASET alias="1">',
    )

    with pytest.raises(ValueError, match="Failed to extract dataset accession"):
        list(extract_dataset_documents(str(root)))


def test_extract_image_id_with_accession_and_alias(tmp_path):
    root = _copy_xml_dir(tmp_path)
    _replace_in_xml(
        root / "METADATA" / "image.xml",
        '<IMAGE alias="2" accession="image_2">',
        '<IMAGE alias="2">',
    )

    docs = {doc.id: doc for doc in extract_dataset_documents(str(root))}

    # Only the first image has an accession. The second document id becomes
    # dataset accession followed by image alias.
    assert set(docs) == {"image_1", "dataset_1-2"}
    opensearch_doc = build_document(docs["dataset_1-2"].values)
    assert opensearch_doc["image_id"] == "2"
    assert opensearch_doc["dataset_id"] == "dataset_1"


def test_extract_diagnosis_with_accession_and_alias(tmp_path):
    root = _copy_xml_dir(tmp_path)
    _replace_in_xml(
        root / "METADATA" / "sample.xml",
        '<PART_OF_CASE_REF alias="1" accession="case_1"/>',
        '<PART_OF_CASE_REF alias="1"/>',
    )
    _replace_in_xml(
        root / "METADATA" / "observation.xml",
        '<CASE_REF alias="1" accession="case_1"/>',
        '<CASE_REF alias="1"/>',
    )

    docs = {doc.id: doc for doc in extract_dataset_documents(str(root))}

    for image_id in ("image_1", "image_2"):
        opensearch_doc = build_document(docs[image_id].values)
        assert "254837009" in opensearch_doc["diagnosis"], image_id


def test_matches_scheme():
    assert _matches_scheme("SNOMED CT", SNOMED_ONTOLOGY_ID)
    assert _matches_scheme("SNOMED-CT", SNOMED_ONTOLOGY_ID)
    assert _matches_scheme("SNOMED_CT", SNOMED_ONTOLOGY_ID)
    assert _matches_scheme("SNOMEDCT", SNOMED_ONTOLOGY_ID)
    assert _matches_scheme("SCT", SNOMED_ONTOLOGY_ID)
    assert _matches_scheme("SNOMED", SNOMED_ONTOLOGY_ID)
    assert _matches_scheme("snomedct", SNOMED_ONTOLOGY_ID)
    assert _matches_scheme(" SCT ", SNOMED_ONTOLOGY_ID)
    assert not _matches_scheme("ICDO", SNOMED_ONTOLOGY_ID)
    assert not _matches_scheme(None, SNOMED_ONTOLOGY_ID)
    assert not _matches_scheme("SNOMED CT", "UNKNOWN")


def test_require_scheme():
    # No value.
    assert _require_scheme(None, SNOMED_ONTOLOGY_ID) is None

    # Matching scheme.
    value = _code("SNOMED CT")
    assert _require_scheme(value, SNOMED_ONTOLOGY_ID) == value

    # Other scheme.
    value = _code("ICDO")
    result = _require_scheme(value, SNOMED_ONTOLOGY_ID)
    assert result is None


def test_filter_by_scheme():
    matching = _code("SNOMED CT")
    mismatched = _code("ICDO")
    result = _filter_by_scheme([matching, mismatched], SNOMED_ONTOLOGY_ID)
    assert result == frozenset([matching])


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

    attribute = _extract_code_attribute_value(elem, "animal_species", None)

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


def test_extract_age_at_extraction_range_valid_one_specimen():
    xml = """
    <SPECIMEN>
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
    </SPECIMEN>
    """
    elem = etree.fromstring(xml)

    result = _extract_age_at_extraction_range(elem)

    assert result == ("P40Y", "P41Y")


def test_extract_age_at_extraction_range_valid_two_specimen():
    xml = """
    <SAMPLE_SET>
        <SPECIMEN alias="1">
            <ATTRIBUTES>
                <SET_ATTRIBUTE>
                    <TAG>age_at_extraction</TAG>
                    <VALUE>
                        <STRING_ATTRIBUTE>
                            <TAG>interval_start</TAG>
                            <VALUE>P10Y</VALUE>
                        </STRING_ATTRIBUTE>
                        <STRING_ATTRIBUTE>
                            <TAG>interval_length</TAG>
                            <VALUE>P1Y</VALUE>
                        </STRING_ATTRIBUTE>
                    </VALUE>
                </SET_ATTRIBUTE>
            </ATTRIBUTES>
        </SPECIMEN>
        <SPECIMEN alias="2">
            <ATTRIBUTES>
                <SET_ATTRIBUTE>
                    <TAG>age_at_extraction</TAG>
                    <VALUE>
                        <STRING_ATTRIBUTE>
                            <TAG>interval_start</TAG>
                            <VALUE>P50Y</VALUE>
                        </STRING_ATTRIBUTE>
                        <STRING_ATTRIBUTE>
                            <TAG>interval_length</TAG>
                            <VALUE>P1Y</VALUE>
                        </STRING_ATTRIBUTE>
                    </VALUE>
                </SET_ATTRIBUTE>
            </ATTRIBUTES>
        </SPECIMEN>
    </SAMPLE_SET>
    """
    elem = etree.fromstring(xml)
    specimen_1, specimen_2 = elem.xpath("/SAMPLE_SET/SPECIMEN")

    assert _extract_age_at_extraction_range(specimen_1) == ("P10Y", "P11Y")
    assert _extract_age_at_extraction_range(specimen_2) == ("P50Y", "P51Y")


def test_extract_age_at_extraction_range_invalid(caplog):

    xml = """
    <SPECIMEN>
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
    </SPECIMEN>
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
    <SPECIMEN>
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
    </SPECIMEN>
    """
    elem = etree.fromstring(xml)

    start, end = _extract_age_at_extraction_range(elem)

    assert start == "P40Y"
    assert end == "P41Y"

    # PT0S interval length — end equals start
    xml = """
     <SPECIMEN>
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
     </SPECIMEN>
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

    values = _extract_code_attribute_values(elem, "anatomical_site", None)

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

    values = _extract_code_attribute_values(elem, "anatomical_site", None)

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
                            <SCHEME>SNOMED CT</SCHEME>
                            <MEANING>Test2</MEANING>
                            <SCHEME_VERSION/>
                        </VALUE>
                    </CODE_ATTRIBUTE>
                    <CODE_ATTRIBUTE>
                        <TAG>anatomical_site</TAG>
                        <VALUE>
                            <CODE>8</CODE>
                            <SCHEME>SNOMED CT</SCHEME>
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
                code="2", scheme="SNOMED CT", meaning="Test2", scheme_version=""
            ),
            BigpictureCodeAttributeValue(
                code="8", scheme="SNOMED CT", meaning="Test8", scheme_version=""
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
                    <SCHEME>SNOMED CT</SCHEME>
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
                            <SCHEME>SNOMED CT</SCHEME>
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
                code="1", scheme="SNOMED CT", meaning="Test1", scheme_version=""
            ),
            BigpictureCodeAttributeValue(
                code="2", scheme="SNOMED CT", meaning="Test2", scheme_version=""
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
                    <SCHEME>SNOMED CT</SCHEME>
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
        code="3", scheme="SNOMED CT", meaning="Test3", scheme_version=""
    )
    assert fixation_type_text is None


def test_extract_fixation_type_unsupported_scheme():
    xml = """
    <ROOT>
        <ATTRIBUTES>
            <CODE_ATTRIBUTE>
                <TAG>fixation_type</TAG>
                <VALUE>
                    <CODE>3</CODE>
                    <SCHEME>ICDO</SCHEME>
                    <MEANING>Test3</MEANING>
                    <SCHEME_VERSION/>
                </VALUE>
            </CODE_ATTRIBUTE>
        </ATTRIBUTES>
    </ROOT>
    """
    elem = etree.fromstring(xml)

    fixation_type, fixation_type_text = _extract_fixation_type(elem)
    assert fixation_type is None
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
