import pytest
from lxml import etree
from lxml.etree import (
    _Element as Element,
    _ElementTree as ElementTree,
)

from search_api.api.beacon.models import SNOMED_ONTOLOGY_ID
from search_api.api.bigpicture.extract.values import (
    _FIELD_ID_BY_XML_TAG,
    _XML_TAG_BY_FIELD_ID,
    _field_id,
    _xml_tag,
    extract_sample_biological_being_fields,
    extract_sample_block_fields,
    extract_sample_specimen_fields,
    extract_diagnoses,
    extract_finding,
    extract_scope,
    extract_staining_fields,
    _extract_iso8601_duration,
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
from search_api.api.bigpicture.extract.models import (
    BigpictureCodeAttributeValue,
    BigpictureSampleBiologicalBeingFields,
    BigpictureSampleBlockFields,
    BigpictureSampleSpecimenFields,
    ObjectIds,
)
from search_api.exceptions import UserException
from search_api.services.ontology.send import SEND_ONTOLOGY_ID
from search_api.api.extract_logs import (
    ExtractLog,
    invalid_duration_log,
    invalid_scheme_log,
    repeated_value_log,
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


def test_extract_iso8601_duration():
    assert _extract_iso8601_duration("P40Y", "P1Y") == "P41Y"
    assert _extract_iso8601_duration("P40Y", "PT0S") == "P40Y"
    assert _extract_iso8601_duration("P40Y", "P6M") == "P40Y6M"
    assert _extract_iso8601_duration("P40Y6M", "P6M") == "P41Y"
    assert _extract_iso8601_duration("P1Y", "P11M") == "P1Y11M"
    assert _extract_iso8601_duration("P1Y", "P12M") == "P2Y"
    assert _extract_iso8601_duration("PT0S", "P1Y") == "P1Y"


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


_STAINING_ALIAS = "stain-1"


def _staining(children: str) -> Element:
    return etree.fromstring(
        f'<STAINING alias="{_STAINING_ALIAS}">{children}</STAINING>'
    )


def test_extract_staining_fields_from_procedure_information():
    staining = _staining("""
        <PROCEDURE_INFORMATION>
            <CODE_ATTRIBUTE>
                <TAG>staining_procedure</TAG>
                <VALUE>
                    <CODE>6</CODE>
                    <SCHEME>SNOMED CT</SCHEME>
                    <MEANING>H and E stain</MEANING>
                </VALUE>
            </CODE_ATTRIBUTE>
        </PROCEDURE_INFORMATION>
        <STAIN>
            <STRING_ATTRIBUTE>
                <TAG>staining_procedure</TAG>
                <VALUE>ignored</VALUE>
            </STRING_ATTRIBUTE>
        </STAIN>
    """)

    extracted = extract_staining_fields(staining)

    assert extracted.ids.alias == _STAINING_ALIAS
    assert extracted.logs == []
    # PROCEDURE_INFORMATION and STAIN are mutually exclusive.
    assert len(extracted.fields) == 1
    fields = extracted.fields[0]
    assert fields.staining_procedure is not None
    assert fields.staining_procedure.code == "6"
    assert fields.staining_procedure_other is None
    assert fields.staining_substance is None
    assert fields.staining_target is None


def test_extract_staining_fields_staining_substance():
    staining = _staining("""
        <STAIN>
            <STRING_ATTRIBUTE>
                <TAG>staining_method</TAG>
                <VALUE>chemical</VALUE>
            </STRING_ATTRIBUTE>
            <CODE_ATTRIBUTE>
                <TAG>staining_compound</TAG>
                <VALUE>
                    <CODE>7</CODE>
                    <SCHEME>SNOMED CT</SCHEME>
                    <MEANING>Eosin</MEANING>
                </VALUE>
            </CODE_ATTRIBUTE>
            <STRING_ATTRIBUTE>
                <TAG>staining_compound</TAG>
                <VALUE>eosin Y</VALUE>
            </STRING_ATTRIBUTE>
            <CODE_ATTRIBUTE>
                <TAG>staining_target</TAG>
                <VALUE>
                    <CODE>8</CODE>
                    <MEANING>not read for a chemical stain</MEANING>
                </VALUE>
            </CODE_ATTRIBUTE>
        </STAIN>
    """)

    (fields,) = extract_staining_fields(staining).fields

    assert fields.staining_substance is not None
    assert fields.staining_substance.code == "7"
    # The code takes precedence, so its free text is not kept beside it.
    assert fields.staining_substance_other is None


def test_extract_staining_fields_staining_target():
    staining = _staining("""
        <STAIN>
            <CODE_ATTRIBUTE>
                <TAG>staining_target</TAG>
                <VALUE>
                    <CODE>9</CODE>
                    <SCHEME>SNOMED CT</SCHEME>
                    <MEANING>pan Cytokeratin</MEANING>
                </VALUE>
            </CODE_ATTRIBUTE>
        </STAIN>
        <STAIN>
            <STRING_ATTRIBUTE>
                <TAG>staining_target</TAG>
                <VALUE>Ki-67</VALUE>
            </STRING_ATTRIBUTE>
        </STAIN>
    """)

    extracted = extract_staining_fields(staining)

    assert [fields.staining_target for fields in extracted.fields] == [
        "pan Cytokeratin",
        "Ki-67",
    ]


_OBSERVATION_TYPE = "confirmed"


def _statement(children: str) -> Element:
    return etree.fromstring(f"<STATEMENT>{children}</STATEMENT>")


def _send_code(tag: str, code: str, meaning: str, scheme: str = "SEND") -> str:
    return f"""
        <CODE_ATTRIBUTE>
            <TAG>{tag}</TAG>
            <VALUE>
                <CODE>{code}</CODE>
                <SCHEME>{scheme}</SCHEME>
                <MEANING>{meaning}</MEANING>
            </VALUE>
        </CODE_ATTRIBUTE>
    """


def test_extract_finding():
    statement = _statement(f"""
        <CODE_ATTRIBUTES>
            {_send_code("MISTRESC", "C3137", "Inflammation")}
            {_send_code("MISEV", "C147501", "Mild")}
            {_send_code("MICHRON", "C14141", "Chronic")}
            {_send_code("MIDISTR", "C25253", "Multifocal")}
            {_send_code("MIRESCAT", "C53529", "Present")}
        </CODE_ATTRIBUTES>
    """)
    finding, logs = extract_finding(statement, _OBSERVATION_TYPE)

    assert finding is not None
    assert finding.finding is not None and finding.finding.code == "C3137"
    assert finding.finding_severity is not None
    assert finding.finding_severity.code == "C147501"
    assert finding.finding_chronicity is not None
    assert finding.finding_chronicity.code == "C14141"
    assert finding.finding_distribution is not None
    assert finding.finding_distribution.code == "C25253"
    assert finding.finding_result_category is not None
    assert finding.finding_result_category.code == "C53529"
    assert finding.observation_type == _OBSERVATION_TYPE
    assert logs == []


def test_extract_finding_invalid_ontology():
    """A code with invalid ontology is dropped, leaving the statement with no finding."""
    statement = _statement(f"""
        <CODE_ATTRIBUTES>
            {_send_code("MISTRESC", "73211009", "Inflammation", scheme="SNOMED CT")}
        </CODE_ATTRIBUTES>
    """)
    finding, logs = extract_finding(statement, _OBSERVATION_TYPE)

    assert finding is None
    assert logs == [
        invalid_scheme_log(
            "finding", "73211009", "Inflammation", "SNOMED CT", SEND_ONTOLOGY_ID
        )
    ]


def test_extract_diagnoses():
    statement = _statement(f"""
        <CODE_ATTRIBUTES>
            {_send_code("diagnosis", "73211009", "Diabetes", scheme="SNOMED CT")}
            {_send_code("diagnosis", "38341003", "Hypertension", scheme="SCT")}
            {_send_code("diagnosis", "8500/3", "Duct carcinoma", scheme="ICDO")}
        </CODE_ATTRIBUTES>
    """)
    diagnoses, logs = extract_diagnoses(statement, _OBSERVATION_TYPE)

    assert {item.diagnosis.code for item in diagnoses if item.diagnosis} == {
        "73211009",
        "38341003",
    }
    assert all(item.observation_type == _OBSERVATION_TYPE for item in diagnoses)
    assert logs == [
        invalid_scheme_log(
            "diagnosis", "8500/3", "Duct carcinoma", "ICDO", SNOMED_ONTOLOGY_ID
        )
    ]


def _policy(type_of_dataset: str | None) -> ElementTree:
    attribute = (
        ""
        if type_of_dataset is None
        else f"""
        <STRING_ATTRIBUTE>
            <TAG>type_of_dataset</TAG>
            <VALUE>{type_of_dataset}</VALUE>
        </STRING_ATTRIBUTE>
        """
    )
    return etree.ElementTree(
        etree.fromstring(f"<POLICY><ATTRIBUTES>{attribute}</ATTRIBUTES></POLICY>")
    )


def test_extract_sample_block_fields():
    xml = etree.fromstring("""
    <BLOCK alias="block-1" accession="bb-block-1">
        <ATTRIBUTES>
            <CODE_ATTRIBUTE>
                <TAG>block_preparation</TAG>
                <VALUE>
                    <CODE>5</CODE>
                    <SCHEME>SNOMED CT</SCHEME>
                    <MEANING>Paraffin embedding</MEANING>
                </VALUE>
            </CODE_ATTRIBUTE>
        </ATTRIBUTES>
    </BLOCK>
    """)

    extracted = extract_sample_block_fields(xml)

    assert extracted.ids == ObjectIds(alias="block-1", accession="bb-block-1")
    assert extracted.fields == BigpictureSampleBlockFields(
        block_preparation=BigpictureCodeAttributeValue(
            code="5", scheme="SNOMED CT", meaning="Paraffin embedding"
        )
    )
    assert extracted.logs == []


def test_extract_sample_biological_being_fields_non_clinical():
    xml = etree.fromstring("""
    <BIOLOGICAL_BEING alias="being-1">
        <ATTRIBUTES>
            <CODE_ATTRIBUTE>
                <TAG>animal_species</TAG>
                <VALUE>
                    <CODE>1</CODE>
                    <SCHEME>SNOMED CT</SCHEME>
                    <MEANING>Cat</MEANING>
                </VALUE>
            </CODE_ATTRIBUTE>
            <STRING_ATTRIBUTE>
                <TAG>sex</TAG>
                <VALUE>Male</VALUE>
            </STRING_ATTRIBUTE>
            <STRING_ATTRIBUTE>
                <TAG>control_terminology</TAG>
                <VALUE>CONTROL</VALUE>
            </STRING_ATTRIBUTE>
        </ATTRIBUTES>
    </BIOLOGICAL_BEING>
    """)

    extracted = extract_sample_biological_being_fields(xml, is_clinical=False)

    assert extracted.ids == ObjectIds(alias="being-1")
    assert extracted.fields == BigpictureSampleBiologicalBeingFields(
        animal_species=BigpictureCodeAttributeValue(
            code="1", scheme="SNOMED CT", meaning="Cat"
        ),
        sex="Male",
        control_terminology="CONTROL",
    )
    assert extracted.logs == []


def test_extract_sample_biological_being_fields_clinical():
    xml = etree.fromstring("""
    <BIOLOGICAL_BEING alias="being-1">
        <ATTRIBUTES>
            <CODE_ATTRIBUTE>
                <TAG>animal_species</TAG>
                <VALUE>
                    <CODE>1</CODE>
                    <SCHEME>SNOMED CT</SCHEME>
                    <MEANING>Cat</MEANING>
                </VALUE>
            </CODE_ATTRIBUTE>
            <CODE_ATTRIBUTE>
                <TAG>animal_species</TAG>
                <VALUE>
                    <CODE>2</CODE>
                    <SCHEME>SNOMED CT</SCHEME>
                    <MEANING>Dog</MEANING>
                </VALUE>
            </CODE_ATTRIBUTE>
            <STRING_ATTRIBUTE>
                <TAG>control_terminology</TAG>
                <VALUE>CONTROL</VALUE>
            </STRING_ATTRIBUTE>
            <STRING_ATTRIBUTE>
                <TAG>control_terminology</TAG>
                <VALUE>TREATED</VALUE>
            </STRING_ATTRIBUTE>
        </ATTRIBUTES>
    </BIOLOGICAL_BEING>
    """)

    extracted = extract_sample_biological_being_fields(xml, is_clinical=True)

    assert extracted.fields.animal_species is None
    assert extracted.fields.control_terminology is None
    assert extracted.logs == []


def test_extract_sample_specimen_fields():
    xml = etree.fromstring("""
    <SPECIMEN alias="specimen-1">
        <ATTRIBUTES>
            <CODE_ATTRIBUTE>
                <TAG>anatomical_site</TAG>
                <VALUE>
                    <CODE>2</CODE>
                    <SCHEME>SNOMED CT</SCHEME>
                    <MEANING>Breast</MEANING>
                </VALUE>
            </CODE_ATTRIBUTE>
            <SET_ATTRIBUTE>
                <TAG>anatomical_site_list</TAG>
                <VALUE>
                    <CODE_ATTRIBUTE>
                        <TAG>anatomical_site</TAG>
                        <VALUE>
                            <CODE>3</CODE>
                            <SCHEME>SNOMED CT</SCHEME>
                            <MEANING>Skin</MEANING>
                        </VALUE>
                    </CODE_ATTRIBUTE>
                </VALUE>
            </SET_ATTRIBUTE>
            <CODE_ATTRIBUTE>
                <TAG>specimen_type</TAG>
                <VALUE>
                    <CODE>4</CODE>
                    <SCHEME>SNOMED CT</SCHEME>
                    <MEANING>Tissue specimen</MEANING>
                </VALUE>
            </CODE_ATTRIBUTE>
            <CODE_ATTRIBUTE>
                <TAG>fixation_type</TAG>
                <VALUE>
                    <CODE>10</CODE>
                    <SCHEME>Other</SCHEME>
                    <MEANING>Home-made fixative</MEANING>
                </VALUE>
            </CODE_ATTRIBUTE>
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
    """)

    extracted = extract_sample_specimen_fields(xml)

    assert extracted.ids == ObjectIds(alias="specimen-1")
    assert extracted.fields == BigpictureSampleSpecimenFields(
        # Sites are read both from the field itself and from the list beside it.
        anatomical_site=frozenset(
            {
                BigpictureCodeAttributeValue(
                    code="2", scheme="SNOMED CT", meaning="Breast"
                ),
                BigpictureCodeAttributeValue(
                    code="3", scheme="SNOMED CT", meaning="Skin"
                ),
            }
        ),
        # A scheme of Other declares the value uncoded, so it becomes free text.
        fixation_type=None,
        fixation_type_other="Home-made fixative",
        specimen_type=BigpictureCodeAttributeValue(
            code="4", scheme="SNOMED CT", meaning="Tissue specimen"
        ),
        age_at_extraction=("P40Y", "P41Y"),
    )
    assert extracted.logs == []


# Test scope
#


@pytest.mark.parametrize(
    "type_of_dataset,expected",
    [
        ("Clinical/Anonymized", "clinical"),
        ("Clinical/Pseudonymized", "clinical"),
        ("Non-Clinical/Obscured", "non_clinical"),
        ("Non-Clinical/Cryptonymized", "non_clinical"),
        # The scope is read case-insensitively, and only up to the "/", so a new
        # de-identification method needs no change here.
        ("non-clinical/whatever", "non_clinical"),
        (" CLINICAL /Anonymized", "clinical"),
    ],
)
def test_extract_scope(type_of_dataset, expected):
    assert extract_scope(_policy(type_of_dataset), "policy.xml") == expected


def test_extract_scope_of_an_unsupported_dataset_type():
    with pytest.raises(UserException, match="Unsupported 'type_of_dataset'"):
        extract_scope(_policy("Preclinical/Anonymized"), "policy.xml")


def test_extract_scope_without_the_attribute():
    with pytest.raises(UserException, match="Missing 'type_of_dataset'"):
        extract_scope(_policy(None), "policy.xml")


def test_extract_finding_repeated_finding():
    statement = _statement(f"""
        <CODE_ATTRIBUTES>
            {_send_code("MISEV", "C147501", "Mild")}
            {_send_code("MISEV", "C147502", "Moderate")}
            {_send_code("MISEV", "C147503", "Severe")}
        </CODE_ATTRIBUTES>
    """)

    finding, logs = extract_finding(statement, _OBSERVATION_TYPE)

    assert finding is not None
    # The first value given is used.
    assert finding.finding_severity is not None
    assert finding.finding_severity.code == "C147501"
    assert logs == [
        repeated_value_log(
            "finding_severity", [("C147502", "Moderate"), ("C147503", "Severe")]
        )
    ]


def test_extract_staining_fields_repeated_target():
    staining = _staining("""
        <STAIN>
            <STRING_ATTRIBUTE>
                <TAG>staining_target</TAG>
                <VALUE>Ki-67</VALUE>
            </STRING_ATTRIBUTE>
            <STRING_ATTRIBUTE>
                <TAG>staining_target</TAG>
                <VALUE>p53</VALUE>
            </STRING_ATTRIBUTE>
        </STAIN>
    """)

    extracted = extract_staining_fields(staining)

    (fields,) = extracted.fields
    assert fields.staining_target == "Ki-67"
    assert extracted.logs == [repeated_value_log("staining_target", ["p53"])]


def test_extract_sample_biological_being_fields_repeated_sex():
    xml = etree.fromstring("""
    <BIOLOGICAL_BEING alias="being-1">
        <ATTRIBUTES>
            <STRING_ATTRIBUTE>
                <TAG>sex</TAG>
                <VALUE>Male</VALUE>
            </STRING_ATTRIBUTE>
            <STRING_ATTRIBUTE>
                <TAG>sex</TAG>
                <VALUE>Female</VALUE>
            </STRING_ATTRIBUTE>
        </ATTRIBUTES>
    </BIOLOGICAL_BEING>
    """)

    extracted = extract_sample_biological_being_fields(xml, is_clinical=False)

    assert extracted.fields.sex == "Male"
    assert extracted.logs == [repeated_value_log("sex", ["Female"])]


def test_xml_tag_maps_are_inverses():
    assert len(_FIELD_ID_BY_XML_TAG) == len(_XML_TAG_BY_FIELD_ID), (
        "two fields share an XML tag, so the inverse map dropped one"
    )
    for field_id, xml_tag in _XML_TAG_BY_FIELD_ID.items():
        assert _field_id(xml_tag) == field_id
        assert _xml_tag(field_id) == xml_tag
    # A field the XML calls by its own name is in neither map.
    assert _xml_tag("sex") == "sex"
    assert _field_id("sex") == "sex"
