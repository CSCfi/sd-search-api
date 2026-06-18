from search_api.api.opensearch.document import build_document
from search_api.api.opensearch.models import (
    OpenSearchField,
    OpenSearchFieldValue,
)


def _value(
    field_id, type_, opensearch_field, value, index=0, multivalued=False
) -> OpenSearchFieldValue:
    return OpenSearchFieldValue(
        field=OpenSearchField(
            id=field_id,
            type=type_,
            opensearch_field=opensearch_field,
            multivalued=multivalued,
        ),
        value=value,
        index=index,
    )


def test_build_document_fields_at_root():
    doc = build_document(
        [
            _value("image_id", "keyword", "image_id", "img-1"),
            _value("dataset_image_cnt", "integer", "dataset_image_cnt", 5),
        ]
    )
    assert doc == {"image_id": "img-1", "dataset_image_cnt": 5}


def test_build_document_nested_field():
    doc = build_document(
        [_value("animal_species", "ontology", "blocks.animal_species", "337915000")]
    )
    assert doc == {"blocks": [{"animal_species": "337915000"}]}


def test_build_document__nested_field_with_index():
    doc = build_document(
        [
            _value(
                "animal_species",
                "ontology",
                "blocks.animal_species",
                "337915000",
                index=0,
            ),
            _value("sex", "controlledValue", "blocks.sex", "Female", index=0),
            _value(
                "animal_species",
                "ontology",
                "blocks.animal_species",
                "447612001",
                index=1,
            ),
        ]
    )
    assert doc == {
        "blocks": [
            {"animal_species": "337915000", "sex": "Female"},
            {"animal_species": "447612001"},
        ]
    }


def test_build_document_multivalued_field():
    doc = build_document(
        [
            _value(
                "anatomical_site",
                "ontology",
                "blocks.anatomical_site",
                "80248007",
                multivalued=True,
            ),
            _value(
                "anatomical_site",
                "ontology",
                "blocks.anatomical_site",
                "368209003",
                multivalued=True,
            ),
        ]
    )
    assert doc == {"blocks": [{"anatomical_site": ["80248007", "368209003"]}]}


def test_build_document_multivalued_single_value():
    doc = build_document(
        [
            _value(
                "anatomical_site",
                "ontology",
                "blocks.anatomical_site",
                "80248007",
                multivalued=True,
            )
        ]
    )
    assert doc == {"blocks": [{"anatomical_site": ["80248007"]}]}


def test_build_document_iso8601_range():
    doc = build_document(
        [_value("age", "iso8601Range", "blocks.age_at_extraction", ("P40Y", "P41Y"))]
    )
    assert doc == {"blocks": [{"age_at_extraction": {"gte": 14600, "lte": 14965}}]}
