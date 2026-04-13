from pathlib import Path

from lxml import etree

from search_api.bigpicture.models import (
    BigpictureSampleBiologicalBeingFields,
    BigpictureCodeAttributeValue,
    BigpictureSampleSpecimenFields,
    BigpictureSampleBlockFields,
)
from search_api.bigpicture.process import (
    process_directories,
    _get_code_attribute_value,
    _get_string_attribute_value,
    _get_age_at_extraction_range,
)

TEST_DIR = Path(__file__).resolve().parent.parent.parent / "test_files" / "bigpicture"


def test_process_directories():
    fields_iterator = process_directories(root=str(TEST_DIR))

    fields = next(fields_iterator)

    assert fields is not None
    assert fields.image_id == "image_1"
    assert fields.dataset_id == "dataset_1"
    assert fields.dataset_description == "test_description"

    assert fields.biological_being_fields == [
        BigpictureSampleBiologicalBeingFields(
            species=BigpictureCodeAttributeValue(
                code="1", scheme="Scheme1", meaning="Test1", scheme_version=""
            ),
            sex="Male",
        )
    ]
    assert fields.specimen_fields == [
        BigpictureSampleSpecimenFields(
            anatomical_site=BigpictureCodeAttributeValue(
                code="2", scheme="Scheme2", meaning="Test2", scheme_version=""
            ),
            fixation_type=BigpictureCodeAttributeValue(
                code="3", scheme="Scheme3", meaning="Test3", scheme_version=""
            ),
            specimen_type=BigpictureCodeAttributeValue(
                code="4", scheme="Scheme4", meaning="Test4", scheme_version=""
            ),
            age_at_extraction_range=(40, 41),
        )
    ]
    assert fields.block_fields == [
        BigpictureSampleBlockFields(
            block_preparation=BigpictureCodeAttributeValue(
                code="5", scheme="Scheme5", meaning="Test5", scheme_version=""
            ),
        )
    ]

    fields = next(fields_iterator)

    assert fields is not None
    assert fields.image_id == "image_2"
    assert fields.dataset_id == "dataset_1"
    assert fields.dataset_description == "test_description"

    assert fields.biological_being_fields == [
        BigpictureSampleBiologicalBeingFields(
            species=BigpictureCodeAttributeValue(
                code="1", scheme="Scheme1", meaning="Test1", scheme_version=""
            ),
            sex="Male",
        )
    ]
    assert fields.specimen_fields == [
        BigpictureSampleSpecimenFields(
            anatomical_site=BigpictureCodeAttributeValue(
                code="2", scheme="Scheme2", meaning="Test2", scheme_version=""
            ),
            fixation_type=BigpictureCodeAttributeValue(
                code="3", scheme="Scheme3", meaning="Test3", scheme_version=""
            ),
            specimen_type=BigpictureCodeAttributeValue(
                code="4", scheme="Scheme4", meaning="Test4", scheme_version=""
            ),
            age_at_extraction_range=(40, 41),
        )
    ]
    assert fields.block_fields == [
        BigpictureSampleBlockFields(
            block_preparation=BigpictureCodeAttributeValue(
                code="5", scheme="Scheme5", meaning="Test5", scheme_version=""
            ),
        )
    ]


def test_process_code_attribute():
    xml = """
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
    """
    elem = etree.fromstring(xml)

    attribute = _get_code_attribute_value(elem, "animal_species")

    assert attribute.code == "1"
    assert attribute.meaning == "Cat"


def test_process_string_attribute():
    xml = """
    <ATTRIBUTES>
        <STRING_ATTRIBUTE>
            <TAG>sex</TAG>
            <VALUE>Male</VALUE>
        </STRING_ATTRIBUTE>
    </ATTRIBUTES>
    """
    elem = etree.fromstring(xml)

    value = _get_string_attribute_value(elem, "sex")

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

    start, end = _get_age_at_extraction_range(elem)

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

    start, end = _get_age_at_extraction_range(elem)

    assert start == 40
    assert end == 40
