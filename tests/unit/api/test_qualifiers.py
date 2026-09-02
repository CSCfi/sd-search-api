"""Validation of declarative filtering qualifier configuration."""

import pytest

from search_api.api.beacon.models import BeaconFilteringQualifier
from search_api.api.opensearch.models import OpenSearchField
from search_api.api.qualifiers import (
    QUALIFIERS_FIELD,
    decode_qualifier_value,
    encode_qualifier_value,
    qualifier_fields,
    validate_filtering_qualifiers,
    validate_requested_qualifiers,
)
from search_api.exceptions import ConfigurationException, UserException


def _qualifier(**overrides) -> BeaconFilteringQualifier:
    kwargs = {
        "id": "observation",
        "label": "Observation",
        "values": ["confirmed", "candidate"],
        "groups": ["diagnosis"],
    }
    kwargs.update(overrides)
    return BeaconFilteringQualifier(**kwargs)


def _field(id_: str, group: str | None = None) -> OpenSearchField:
    return OpenSearchField(id=id_, type="keyword", nested_group=group)


_DIAGNOSIS_TERM = _field("diagnosis", "diagnosis")


def test_encode_qualifier_value():
    assert encode_qualifier_value("observation", "confirmed") == "observation:confirmed"


def test_decode_qualifier_value():
    assert decode_qualifier_value("observation:confirmed") == (
        "observation",
        "confirmed",
    )


def test_qualifier_fields_one_per_group():
    """Emitted for every nested group, qualified or not, so adding a qualifier to
    another group needs no index change."""
    fields = qualifier_fields(["finding", "diagnosis"])
    assert [f.opensearch_field for f in fields] == [
        "diagnosis.qualifiers",
        "finding.qualifiers",
    ]
    assert all(f.multivalued and f.type == "keyword" for f in fields)


def test_validate_accepts_qualifier_over_defined_group():
    validate_filtering_qualifiers([_DIAGNOSIS_TERM], [], [_qualifier()], "x")


def test_validate_rejects_unknown_group():
    with pytest.raises(ConfigurationException, match="unknown group"):
        validate_filtering_qualifiers(
            [_DIAGNOSIS_TERM], [], [_qualifier(groups=["nope"])], "x"
        )


def test_validate_rejects_duplicate_qualifier_id():
    with pytest.raises(ConfigurationException, match="defined more than once"):
        validate_filtering_qualifiers(
            [_DIAGNOSIS_TERM], [], [_qualifier(), _qualifier()], "x"
        )


def test_validate_rejects_filtering_term_on_the_reserved_field():
    with pytest.raises(ConfigurationException, match="diagnosis.qualifiers"):
        validate_filtering_qualifiers(
            [_DIAGNOSIS_TERM, _field(QUALIFIERS_FIELD, "diagnosis")],
            [],
            [_qualifier()],
            "x",
        )


def test_validate_rejects_non_filtering_field_on_the_reserved_field():
    """Otherwise build_document silently overwrites it with the qualifier values."""
    with pytest.raises(ConfigurationException, match="diagnosis.qualifiers"):
        validate_filtering_qualifiers(
            [_DIAGNOSIS_TERM],
            [_field(QUALIFIERS_FIELD, "diagnosis")],
            [_qualifier()],
            "x",
        )


def test_validate_allows_reserved_name_at_the_top_level():
    """Only a field inside a group can collide with a group's qualifiers field."""
    validate_filtering_qualifiers(
        [_DIAGNOSIS_TERM], [_field(QUALIFIERS_FIELD)], [_qualifier()], "x"
    )


def test_validate_rejects_separator_in_an_id_or_value():
    with pytest.raises(ConfigurationException, match="reserved separator"):
        validate_filtering_qualifiers(
            [_DIAGNOSIS_TERM], [], [_qualifier(values=["a:b"])], "x"
        )


def test_validate_requested_accepts_declared_value():
    validate_requested_qualifiers({"observation": ["confirmed"]}, [_qualifier()])


def test_validate_requested_accepts_no_qualifier():
    """An absent qualifier is not filtered on, so all of its values match."""
    validate_requested_qualifiers({}, [_qualifier()])


def test_validate_requested_rejects_unknown_qualifier():
    with pytest.raises(UserException, match="Unsupported qualifier: 'certainty'"):
        validate_requested_qualifiers({"certainty": ["known"]}, [_qualifier()])


def test_validate_requested_rejects_undeclared_value():
    with pytest.raises(UserException, match="Unsupported value"):
        validate_requested_qualifiers({"observation": ["known"]}, [_qualifier()])


def test_validate_requested_rejects_several_values_for_one_qualifier():
    with pytest.raises(UserException, match="Several values"):
        validate_requested_qualifiers(
            {"observation": ["confirmed", "candidate"]}, [_qualifier()]
        )
