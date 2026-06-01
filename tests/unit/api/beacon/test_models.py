from search_api.api.beacon.models import (
    BeaconFilteringTerm,
    BeaconFilteringOntology,
    SNOMED_ONTOLOGY_ID,
)

SNOMED_ONTOLOGY = BeaconFilteringOntology(id=SNOMED_ONTOLOGY_ID)


def filtering_term(ontology_concept: str | list[str] | None) -> BeaconFilteringTerm:
    return BeaconFilteringTerm(
        id="test_field",
        type="ontology",
        scopes=["test"],
        ontology=SNOMED_ONTOLOGY,
        ontologyConcept=ontology_concept,
    )


def test_snomed_ecl():
    term = filtering_term("410607006")
    assert term.snomed_ecl == "<< 410607006"
    term = filtering_term(["311731000", "433469005", "61088005"])
    assert term.snomed_ecl == "311731000 OR 433469005 OR 61088005"
    term = filtering_term(None)
    assert term.snomed_ecl is None
    term = filtering_term([])
    assert term.snomed_ecl is None
