from pathlib import Path

from lxml import etree

from search_api.bigpicture.models import (
    BigPictureSampleBiologicalBeingFields,
    BigPictureCodeAttributeValue,
    BigPictureSampleSpecimenFields,
    BigPictureSampleBlockFields,
)
from search_api.bigpicture.process import process_directories, _get_code_attribute_value

TEST_DIR = Path(__file__).resolve().parent.parent.parent / "test_files" / "bigpicture"


def test_process_directories():
    fields_iterator = process_directories(root=str(TEST_DIR))

    fields = next(fields_iterator)

    assert fields is not None
    assert fields.image_id == "image_1"
    assert fields.dataset_id == "dataset_1"
    assert fields.dataset_description == "test_description"

    assert fields.biological_being_fields == [
        BigPictureSampleBiologicalBeingFields(
            species=BigPictureCodeAttributeValue(
                code="1", scheme="Scheme1", meaning="Test1", scheme_version=""
            )
        )
    ]
    assert fields.specimen_fields == [
        BigPictureSampleSpecimenFields(
            anatomical_site=BigPictureCodeAttributeValue(
                code="2", scheme="Scheme2", meaning="Test2", scheme_version=""
            ),
            fixation_type=BigPictureCodeAttributeValue(
                code="3", scheme="Scheme3", meaning="Test3", scheme_version=""
            ),
            specimen_type=BigPictureCodeAttributeValue(
                code="4", scheme="Scheme4", meaning="Test4", scheme_version=""
            ),
        )
    ]
    assert fields.block_fields == [
        BigPictureSampleBlockFields(
            block_preparation=BigPictureCodeAttributeValue(
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
        BigPictureSampleBiologicalBeingFields(
            species=BigPictureCodeAttributeValue(
                code="1", scheme="Scheme1", meaning="Test1", scheme_version=""
            )
        )
    ]
    assert fields.specimen_fields == [
        BigPictureSampleSpecimenFields(
            anatomical_site=BigPictureCodeAttributeValue(
                code="2", scheme="Scheme2", meaning="Test2", scheme_version=""
            ),
            fixation_type=BigPictureCodeAttributeValue(
                code="3", scheme="Scheme3", meaning="Test3", scheme_version=""
            ),
            specimen_type=BigPictureCodeAttributeValue(
                code="4", scheme="Scheme4", meaning="Test4", scheme_version=""
            ),
        )
    ]
    assert fields.block_fields == [
        BigPictureSampleBlockFields(
            block_preparation=BigPictureCodeAttributeValue(
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
