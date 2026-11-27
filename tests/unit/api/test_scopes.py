import pytest

from search_api.api.beacon.models import BeaconFilteringScope, BeaconFilteringTerm
from search_api.api.scopes import validate_filtering_scopes
from search_api.exceptions import ConfigurationException


def _term(field_id, scopes=(), requires=None):
    return BeaconFilteringTerm(
        id=field_id,
        type="keyword",
        scopes=list(scopes),
        label=field_id,
        description=field_id,
        requires=requires,
    )


def _validate_filtering_scopes(terms):
    """Validate the terms against the scopes "scope" and "other_scope"."""
    validate_filtering_scopes(
        terms,
        [BeaconFilteringScope(id=s, label=s) for s in ("scope", "other_scope")],
        "fields.yaml",
    )


@pytest.mark.parametrize(
    "terms",
    [
        # required_field is indexed in both scopes of requires_field.
        [
            _term("required_field", ["scope", "other_scope"]),
            _term("requires_field", ["scope", "other_scope"], ["required_field"]),
        ],
        # Each scope of requires_field is a scope of one of the fields it requires.
        [
            _term("required_field", ["scope"]),
            _term("other_required_field", ["other_scope"]),
            _term(
                "requires_field",
                ["scope", "other_scope"],
                ["required_field", "other_required_field"],
            ),
        ],
        # required_field declares no scopes, so it is indexed in every scope.
        [
            _term("required_field"),
            _term("requires_field", ["scope", "other_scope"], ["required_field"]),
        ],
        # requires_field declares no scopes, so it is indexed in every scope, and
        # the fields it requires are indexed in every scope between them.
        [
            _term("required_field", ["scope"]),
            _term("other_required_field", ["other_scope"]),
            _term(
                "requires_field", requires=["required_field", "other_required_field"]
            ),
        ],
    ],
)
def test_requires_scopes(terms):
    """A field may be indexed in a scope only if a field it requires is too."""
    # Validates without a ConfigurationException.
    _validate_filtering_scopes(terms)


@pytest.mark.parametrize(
    "terms",
    [
        # requires_field declares other_scope, which required_field does not.
        [
            _term("required_field", ["scope"]),
            _term("requires_field", ["scope", "other_scope"], ["required_field"]),
        ],
        # requires_field declares no scopes, so it is indexed in other_scope too.
        [
            _term("required_field", ["scope"]),
            _term("requires_field", requires=["required_field"]),
        ],
    ],
)
def test_requires_scopes_rejected(terms):
    """requires_field is indexed in other_scope, where required_field is not, so it
    would always be dropped there and validation fails."""
    with pytest.raises(
        ConfigurationException,
        match=r"field 'requires_field' is indexed in scope\(s\) \['other_scope'\], "
        "where none of the fields it requires is",
    ):
        _validate_filtering_scopes(terms)
