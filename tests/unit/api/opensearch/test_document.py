import pytest
from pydantic import ValidationError

from search_api.api.opensearch.document import build_document
from search_api.api.opensearch.models import (
    ExtractedDocument,
    OpenSearchField,
    OpenSearchFieldValue,
    OpenSearchGroup,
)


def _build(values=(), groups=(), scope=None) -> dict:
    return build_document(
        ExtractedDocument(
            id="doc-1", values=list(values), groups=list(groups), scope=scope
        )
    )


def _value(field_id, type_, group, value, multivalued=False) -> OpenSearchFieldValue:
    return OpenSearchFieldValue(
        field=OpenSearchField(
            id=field_id,
            type=type_,
            group=group,
            multivalued=multivalued,
        ),
        value=value,
    )


def _group(group, *values, qualifiers=None) -> OpenSearchGroup:
    return OpenSearchGroup(
        group=group, values=list(values), qualifiers=qualifiers or {}
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
    doc = _build(
        groups=[
            _group(
                "blocks", _value("animal_species", "ontology", "blocks", "337915000")
            )
        ]
    )
    assert doc == {"blocks": [{"animal_species": "337915000"}]}


def test_build_document_one_item_per_group():
    """Each group is its own item, so values are only together if the group is."""
    doc = _build(
        groups=[
            _group(
                "blocks",
                _value("animal_species", "ontology", "blocks", "337915000"),
                _value("sex", "controlledValue", "blocks", "Female"),
            ),
            _group(
                "blocks", _value("animal_species", "ontology", "blocks", "447612001")
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
    doc = _build(
        groups=[
            _group(
                "blocks",
                _value(
                    "anatomical_site",
                    "ontology",
                    "blocks",
                    "80248007",
                    multivalued=True,
                ),
                _value(
                    "anatomical_site",
                    "ontology",
                    "blocks",
                    "368209003",
                    multivalued=True,
                ),
            )
        ]
    )
    assert doc == {"blocks": [{"anatomical_site": ["80248007", "368209003"]}]}


def test_build_document_multivalued_single_value():
    doc = _build(
        groups=[
            _group(
                "blocks",
                _value(
                    "anatomical_site",
                    "ontology",
                    "blocks",
                    "80248007",
                    multivalued=True,
                ),
            )
        ]
    )
    assert doc == {"blocks": [{"anatomical_site": ["80248007"]}]}


def test_build_document_iso8601_range():
    doc = _build(
        groups=[
            _group(
                "blocks",
                _value("age_at_extraction", "iso8601Range", "blocks", ("P40Y", "P41Y")),
            )
        ]
    )
    assert doc == {"blocks": [{"age_at_extraction": {"gte": 14600, "lte": 14965}}]}


def test_build_document_scope_is_written_at_the_root():
    doc = _build([_value("image_id", "keyword", None, "img-1")], scope="clinical")
    assert doc == {"image_id": "img-1", "scope": "clinical"}


def test_build_document_without_scope_omits_it():
    assert "scope" not in _build([_value("image_id", "keyword", None, "img-1")])


def test_build_document_writes_every_qualifier_of_an_item_to_one_field():
    """Every qualifier of an item shares one field, each value carrying its id."""
    doc = _build(
        groups=[
            _group(
                "diagnosis",
                _value("diagnosis", "ontology", "diagnosis", "73211009"),
                qualifiers={"observation": "confirmed", "certainty": "high"},
            )
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
        groups=[
            _group(
                "diagnosis",
                _value("diagnosis", "ontology", "diagnosis", "a"),
                qualifiers={"observation": "confirmed"},
            ),
            _group(
                "diagnosis",
                _value("diagnosis", "ontology", "diagnosis", "b"),
                qualifiers={"observation": "candidate"},
            ),
        ]
    )
    assert doc["diagnosis"] == [
        {"diagnosis": "a", "qualifiers": ["observation:confirmed"]},
        {"diagnosis": "b", "qualifiers": ["observation:candidate"]},
    ]


def test_build_document_omits_qualifiers_when_there_are_none():
    doc = _build(
        groups=[
            _group("specimen", _value("sex", "controlledValue", "specimen", "Female"))
        ]
    )
    assert doc == {"specimen": [{"sex": "Female"}]}


def test_group_rejects_a_value_of_another_group():
    """A value's own field names its group, so a mismatch is a misfiled document."""
    with pytest.raises(ValidationError, match="sex are not in group 'diagnosis'"):
        _group("diagnosis", _value("sex", "controlledValue", "specimen", "Female"))
