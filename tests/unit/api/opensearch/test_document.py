from search_api.api.opensearch.document import build_document
from search_api.api.opensearch.models import (
    ExtractedDocument,
    OpenSearchField,
    OpenSearchFieldValue,
)


def _build(values, scope=None) -> dict:
    return build_document(ExtractedDocument(id="doc-1", values=values, scope=scope))


def _value(
    field_id, type_, group, value, index=0, multivalued=False, qualifiers=None
) -> OpenSearchFieldValue:
    return OpenSearchFieldValue(
        field=OpenSearchField(
            id=field_id,
            type=type_,
            group=group,
            multivalued=multivalued,
        ),
        value=value,
        index=index,
        qualifiers=qualifiers or {},
    )


def test_build_document_fields_at_root():
    doc = _build(
        [
            _value("image_id", "keyword", None, "img-1"),
            _value("dataset_image_cnt", "integer", None, 5),
        ]
    )
    assert doc == {"image_id": "img-1", "dataset_image_cnt": 5}


def test_build_document_nested_field():
    doc = _build([_value("animal_species", "ontology", "blocks", "337915000")])
    assert doc == {"blocks": [{"animal_species": "337915000"}]}


def test_build_document__nested_field_with_index():
    doc = _build(
        [
            _value("animal_species", "ontology", "blocks", "337915000", index=0),
            _value("sex", "controlledValue", "blocks", "Female", index=0),
            _value("animal_species", "ontology", "blocks", "447612001", index=1),
        ]
    )
    assert doc == {
        "blocks": [
            {"animal_species": "337915000", "sex": "Female"},
            {"animal_species": "447612001"},
        ]
    }


def test_build_document_multivalued_field():
    doc = _build(
        [
            _value(
                "anatomical_site", "ontology", "blocks", "80248007", multivalued=True
            ),
            _value(
                "anatomical_site", "ontology", "blocks", "368209003", multivalued=True
            ),
        ]
    )
    assert doc == {"blocks": [{"anatomical_site": ["80248007", "368209003"]}]}


def test_build_document_multivalued_single_value():
    doc = _build(
        [_value("anatomical_site", "ontology", "blocks", "80248007", multivalued=True)]
    )
    assert doc == {"blocks": [{"anatomical_site": ["80248007"]}]}


def test_build_document_iso8601_range():
    doc = _build(
        [_value("age_at_extraction", "iso8601Range", "blocks", ("P40Y", "P41Y"))]
    )
    assert doc == {"blocks": [{"age_at_extraction": {"gte": 14600, "lte": 14965}}]}


def test_build_document_scope_is_written_at_the_root():
    doc = _build([_value("image_id", "keyword", None, "img-1")], scope="clinical")
    assert doc == {"image_id": "img-1", "scope": "clinical"}


def test_build_document_without_scope_omits_it():
    assert "scope" not in _build([_value("image_id", "keyword", None, "img-1")])


def test_build_document_merges_qualifiers_of_one_nested_item():
    """Every qualifier of an item shares one field, each value carrying its id."""
    doc = _build(
        [
            _value(
                "diagnosis",
                "ontology",
                "diagnosis",
                "73211009",
                qualifiers={"observation": ["confirmed"], "certainty": ["high"]},
            ),
        ]
    )
    assert doc == {
        "diagnosis": [
            {
                "diagnosis": "73211009",
                "qualifiers": ["certainty:high", "observation:confirmed"],
            }
        ]
    }


def test_build_document_qualifiers_stay_with_their_own_nested_item():
    doc = _build(
        [
            _value(
                "diagnosis",
                "ontology",
                "diagnosis",
                "a",
                index=0,
                qualifiers={"observation": ["confirmed"]},
            ),
            _value(
                "diagnosis",
                "ontology",
                "diagnosis",
                "b",
                index=1,
                qualifiers={"observation": ["candidate"]},
            ),
        ]
    )
    assert doc["diagnosis"] == [
        {"diagnosis": "a", "qualifiers": ["observation:confirmed"]},
        {"diagnosis": "b", "qualifiers": ["observation:candidate"]},
    ]


def test_build_document_omits_qualifiers_when_there_are_none():
    doc = _build([_value("sex", "controlledValue", "specimen", "Female")])
    assert doc == {"specimen": [{"sex": "Female"}]}
