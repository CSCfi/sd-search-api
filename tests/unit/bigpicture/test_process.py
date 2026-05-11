from pathlib import Path

from lxml import etree

from search_api.bigpicture.models import (
    BigpictureCodeAttributeValue,
    BigpictureBlockFields,
    BigpictureStainingFields,
)
from search_api.bigpicture.process import (
    extract_fields,
    _extract_code_attribute_value,
    _extract_string_attribute_value,
    _extract_age_at_extraction_range,
)

TEST_DIR = Path(__file__).resolve().parent.parent.parent / "test_files" / "bigpicture"


def test_extract_fields():
    fields_iterator = extract_fields(root=str(TEST_DIR))
    for fields in fields_iterator:
        if fields.image_id == "image_1":
            assert fields is not None
            assert fields.image_id == "image_1"
            assert fields.dataset_id == "dataset_1"
            assert fields.dataset_description == "test_description"
            assert fields.blocks == {
                BigpictureBlockFields(
                    block_preparation=BigpictureCodeAttributeValue(
                        code="5", scheme="Scheme5", meaning="Test5", scheme_version=""
                    ),
                    species=BigpictureCodeAttributeValue(
                        code="1", scheme="Scheme1", meaning="Test1", scheme_version=""
                    ),
                    sex="Male",
                    anatomical_site=BigpictureCodeAttributeValue(
                        code="2", scheme="Scheme2", meaning="Test2", scheme_version=""
                    ),
                    fixation_type=BigpictureCodeAttributeValue(
                        code="3", scheme="Scheme3", meaning="Test3", scheme_version=""
                    ),
                    specimen_type=BigpictureCodeAttributeValue(
                        code="4", scheme="Scheme4", meaning="Test4", scheme_version=""
                    ),
                    age_at_extraction=(40, 41),
                )
            }

            assert fields.stains == {
                BigpictureStainingFields(
                    staining_method="chemical",
                    staining_procedure=BigpictureCodeAttributeValue(
                        code="6", scheme="Scheme6", meaning="Test6", scheme_version=""
                    ),
                    staining_procedure_text="test6",
                    staining_target=None,
                )
            }
        else:
            assert fields is not None
            assert fields.image_id == "image_2"
            assert fields.dataset_id == "dataset_1"
            assert fields.dataset_description == "test_description"

            assert fields.blocks == {
                BigpictureBlockFields(
                    block_preparation=BigpictureCodeAttributeValue(
                        code="5", scheme="Scheme5", meaning="Test5", scheme_version=""
                    ),
                    species=BigpictureCodeAttributeValue(
                        code="1", scheme="Scheme1", meaning="Test1", scheme_version=""
                    ),
                    sex="Male",
                    anatomical_site=BigpictureCodeAttributeValue(
                        code="2", scheme="Scheme2", meaning="Test2", scheme_version=""
                    ),
                    fixation_type=BigpictureCodeAttributeValue(
                        code="3", scheme="Scheme3", meaning="Test3", scheme_version=""
                    ),
                    specimen_type=BigpictureCodeAttributeValue(
                        code="4", scheme="Scheme4", meaning="Test4", scheme_version=""
                    ),
                    age_at_extraction=(40, 41),
                )
            }

            assert fields.stains == {
                BigpictureStainingFields(
                    staining_method="immunogenic",
                    staining_procedure=BigpictureCodeAttributeValue(
                        code="7", scheme="Scheme7", meaning="Test7", scheme_version=""
                    ),
                    staining_procedure_text="test7",
                    staining_target="pan Cytokeratin",
                    staining_compound_text="antibody",
                )
            }


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

    assert start == 40
    assert end == 41

    # PT0S interval length
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

    assert start == 40
    assert end == 40
