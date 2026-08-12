import pytest

from search_api.api.beacon.models import BeaconFilteringGroup, BeaconFilteringTerm
from search_api.api.bigpicture.models import (
    _GROUPS_CONFIG_PATH as _BP_GROUPS_CONFIG_PATH,
)
from search_api.api.groups import load_groups_config, validate_filtering_groups
from search_api.exceptions import ConfigurationException


def _term(field_id: str, ui_group: str | None) -> BeaconFilteringTerm:
    return BeaconFilteringTerm(
        id=field_id,
        type="text",
        scopes=["dataset"],
        label=field_id,
        description=field_id,
        ui_group=ui_group,
    )


def _group(group_id: str) -> BeaconFilteringGroup:
    return BeaconFilteringGroup(id=group_id, label=group_id)


def test_all_filtering_groups_defined():
    terms = [_term("title", "description"), _term("species", "subject")]
    groups = [_group("description"), _group("subject")]
    # Should not raise.
    validate_filtering_groups(terms, groups, "fields.yaml")


def test_terms_without_filtering_group_are_ignored():
    terms = [_term("title", None)]
    groups = [_group("description")]
    # Should not raise.
    validate_filtering_groups(terms, groups, "fields.yaml")


def test_unknown_filtering_group_raises():
    terms = [_term("title", "description"), _term("species", "missing")]
    groups = [_group("description")]
    with pytest.raises(ConfigurationException) as exc:
        validate_filtering_groups(terms, groups, "fields.yaml")
    message = str(exc.value)
    assert "fields.yaml" in message
    assert "missing" in message
    assert "description" in message


def test_multiple_unknown_filtering_groups_reported():
    terms = [_term("a", "x"), _term("b", "y")]
    groups = [_group("description")]
    with pytest.raises(ConfigurationException) as exc:
        validate_filtering_groups(terms, groups, "fields.yaml")
    message = str(exc.value)
    assert "'x'" in message
    assert "'y'" in message


def test_border_defaults_to_false():
    """Only a group that asks for one gets a border, so config alone adds it."""
    assert _group("description").border is False


def test_bigpicture_groups_with_border():
    """A canary: it is the UI that draws them, so a change here is a visual change."""
    groups = load_groups_config(_BP_GROUPS_CONFIG_PATH).filtering_groups

    assert {group.id for group in groups if group.border} == {
        "staining",
        "clinical",
        "non_clinical",
    }
