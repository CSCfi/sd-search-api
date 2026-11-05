import pytest
from pydantic import ValidationError

from search_api.api.beacon.models import (
    MAX_FILTER_VALUE_LENGTH,
    MAX_FILTER_VALUES,
    MAX_FILTERS,
    MAX_ID_LENGTH,
    SNOMED_ONTOLOGY_ID,
    BeaconFilteringOntology,
    BeaconFilteringTerm,
    BeaconQuery,
    BeaconQueryFilter,
    OntologyRestriction,
)

SNOMED_ONTOLOGY = BeaconFilteringOntology(id=SNOMED_ONTOLOGY_ID)


def filtering_term(restriction: OntologyRestriction | None) -> BeaconFilteringTerm:
    return BeaconFilteringTerm(
        id="test_field",
        label="test",
        description="test",
        type="ontology",
        scopes=["test"],
        ontology=SNOMED_ONTOLOGY,
        ontologyRestriction=restriction,
    )


def test_snomed_ecl_includes_descendants():
    term = filtering_term(
        OntologyRestriction(concept_ids=["410607006"], include_descendants=True)
    )
    assert term.snomed_ecl == "<< 410607006"
    term = filtering_term(
        OntologyRestriction(
            concept_ids=["311731000", "433469005"], include_descendants=True
        ),
    )
    assert term.snomed_ecl == "<< 311731000 OR << 433469005"


def test_snomed_ecl_excludes_descendants():
    term = filtering_term(
        OntologyRestriction(
            concept_ids=["311731000", "433469005", "61088005"],
            include_descendants=False,
        )
    )
    assert term.snomed_ecl == "311731000 OR 433469005 OR 61088005"


def test_snomed_ecl_is_none_without_an_ontology_restriction():
    """A field without a restriction resolves against the whole ontology."""
    assert filtering_term(None).snomed_ecl is None


def test_ontology_restriction_requires_a_concept_id():
    with pytest.raises(ValidationError):
        OntologyRestriction(concept_ids=[], include_descendants=True)


# Query limits
#


def test_query_at_limit_is_accepted():
    value = "v" * MAX_FILTER_VALUE_LENGTH
    query = BeaconQuery(
        filters=[
            BeaconQueryFilter(id="x" * MAX_ID_LENGTH, value=[value] * MAX_FILTER_VALUES)
        ]
        * MAX_FILTERS,
        requestedScope="x" * MAX_ID_LENGTH,
    )
    assert len(query.filters) == MAX_FILTERS


@pytest.mark.parametrize(
    "query",
    [
        {"filters": [{"id": "id", "value": "v"}] * (MAX_FILTERS + 1)},
        {"filters": [{"id": "x" * (MAX_ID_LENGTH + 1), "value": "v"}]},
        {"filters": [{"id": "id", "value": "v" * (MAX_FILTER_VALUE_LENGTH + 1)}]},
        {"filters": [{"id": "id", "value": ["v"] * (MAX_FILTER_VALUES + 1)}]},
        {
            "filters": [
                {"id": "id", "value": ["v", "v" * (MAX_FILTER_VALUE_LENGTH + 1)]}
            ]
        },
        {"requestedScope": "x" * (MAX_ID_LENGTH + 1)},
    ],
    ids=["filters", "id", "value", "values", "value in list", "scope"],
)
def test_query_over_limit_is_rejected(query):
    with pytest.raises(ValidationError):
        BeaconQuery.model_validate(query)
