from lxml import etree

from search_api.api.beacon.models import SNOMED_ONTOLOGY_ID
from search_api.api.bigpicture.extract.attributes import (
    _iso8601_duration,
    _extract_age_at_extraction,
    _extract_anatomical_sites,
    _extract_code_attribute_value,
    _extract_code_attribute_values,
    _extract_fixation_type,
    _extract_string_attribute_value,
    _filter_values_by_scheme,
    _matches_scheme,
    _filter_value_by_scheme,
)
from search_api.api.bigpicture.extract.models import BigpictureCodeAttributeValue
from search_api.api.extract_logs import (
    ExtractLog,
    invalid_duration_log,
    invalid_scheme_log,
)


def _code(scheme: str | None) -> BigpictureCodeAttributeValue:
    return BigpictureCodeAttributeValue(code="1", scheme=scheme, meaning="Test")


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


def test_filter_value_by_scheme_error():
    logs: list[ExtractLog] = []

    # No value.
    assert (
        _filter_value_by_scheme(None, SNOMED_ONTOLOGY_ID, "animal_species", logs)
        is None
    )

    # Matching scheme.
    value = _code("SNOMED CT")
    assert (
        _filter_value_by_scheme(value, SNOMED_ONTOLOGY_ID, "animal_species", logs)
        == value
    )
    assert logs == []

    # Other scheme: dropped, and recorded for the document.
    value = _code("ICDO")
    assert (
        _filter_value_by_scheme(value, SNOMED_ONTOLOGY_ID, "animal_species", logs)
        is None
    )
    assert logs == [
        invalid_scheme_log("animal_species", "1", "Test", "ICDO", SNOMED_ONTOLOGY_ID)
    ]


def test_filter_values_by_scheme():
    matching = _code("SNOMED CT")
    mismatched = _code("ICDO")
    logs: list[ExtractLog] = []

    result = _filter_values_by_scheme(
        [matching, mismatched], SNOMED_ONTOLOGY_ID, "anatomical_site", logs
    )

    assert result == frozenset([matching])
    assert len(logs) == 1
    assert "Value ('1', 'Test') is ignored" in logs[0].message


def test_extract_code_attribute_value_without_ontology():
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

    value = _extract_code_attribute_value(elem, "animal_species", None, [])

    assert value is not None
    assert value.code == "1"
    assert value.meaning == "Cat"


def test_extract_code_attribute_value_with_ontology():
    xml = """
    <ROOT>
        <ATTRIBUTES>
            <CODE_ATTRIBUTE>
                <TAG>specimen_type</TAG>
                <VALUE>
                    <CODE>5</CODE>
                    <SCHEME>SNOMED CT</SCHEME>
                    <MEANING>Tissue specimen</MEANING>
                </VALUE>
            </CODE_ATTRIBUTE>
        </ATTRIBUTES>
    </ROOT>
    """
    logs: list[ExtractLog] = []

    value = _extract_code_attribute_value(
        etree.fromstring(xml), "specimen_type", SNOMED_ONTOLOGY_ID, logs
    )

    assert value is not None
    assert (value.code, value.meaning) == ("5", "Tissue specimen")
    assert logs == []


def test_extract_code_attribute_value_missing_ontology():
    xml = """
    <ROOT>
        <ATTRIBUTES>
            <CODE_ATTRIBUTE>
                <TAG>specimen_type</TAG>
                <VALUE>
                    <CODE>5</CODE>
                    <MEANING>Tissue specimen</MEANING>
                </VALUE>
            </CODE_ATTRIBUTE>
        </ATTRIBUTES>
    </ROOT>
    """
    logs: list[ExtractLog] = []

    value = _extract_code_attribute_value(
        etree.fromstring(xml), "specimen_type", SNOMED_ONTOLOGY_ID, logs
    )

    assert value is None
    assert logs == [
        invalid_scheme_log(
            "specimen_type", "5", "Tissue specimen", None, SNOMED_ONTOLOGY_ID
        )
    ]


def test_extract_string_attribute_value():
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


def test_extract_age_at_extraction_one_specimen():
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

    result = _extract_age_at_extraction(elem, [])

    assert result == ("P40Y", "P41Y")


def test_extract_age_at_extraction_two_specimen():
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

    assert _extract_age_at_extraction(specimen_1, []) == ("P10Y", "P11Y")
    assert _extract_age_at_extraction(specimen_2, []) == ("P50Y", "P51Y")


def test_extract_age_at_extraction_error(caplog):

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

    logs: list[ExtractLog] = []

    result = _extract_age_at_extraction(elem, logs)

    assert result is None
    assert logs == [invalid_duration_log("age_at_extraction", ("NOT_VALID", "P1Y"))]


def test_iso8601_duration():
    assert _iso8601_duration("P40Y", "P1Y") == "P41Y"
    assert _iso8601_duration("P40Y", "PT0S") == "P40Y"
    assert _iso8601_duration("P40Y", "P6M") == "P40Y6M"
    assert _iso8601_duration("P40Y6M", "P6M") == "P41Y"
    assert _iso8601_duration("P1Y", "P11M") == "P1Y11M"
    assert _iso8601_duration("P1Y", "P12M") == "P2Y"
    assert _iso8601_duration("PT0S", "P1Y") == "P1Y"


def test_extract_age_at_extraction_range():
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

    start, end = _extract_age_at_extraction(elem, [])

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

    start, end = _extract_age_at_extraction(elem, [])

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

    values = _extract_code_attribute_values(elem, "anatomical_site", None, [])

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

    values = _extract_code_attribute_values(elem, "anatomical_site", None, [])

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

    sites = _extract_anatomical_sites(elem, [])

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

    sites = _extract_anatomical_sites(elem, [])

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


def test_extract_fixation_type_expected_scheme():
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

    fixation_type, fixation_type_text = _extract_fixation_type(elem, [])

    assert fixation_type == BigpictureCodeAttributeValue(
        code="3", scheme="SNOMED CT", meaning="Test3", scheme_version=""
    )
    assert fixation_type_text is None


def test_extract_fixation_type_invalid_scheme():
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

    fixation_type, fixation_type_text = _extract_fixation_type(elem, [])
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

    fixation_type, fixation_type_text = _extract_fixation_type(elem, [])

    assert fixation_type is None
    assert fixation_type_text == "Test7"
