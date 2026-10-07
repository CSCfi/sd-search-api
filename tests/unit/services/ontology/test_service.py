"""Unit tests for the shared prepare_ontology_filter template method.

``prepare_ontology_filter`` is implemented once on ``OntologyService`` with
``_find_concept_ids`` and ``_find_descendant_ids`` hooks. The template
method is tested here against a mock provider whose hooks are plain lookup
tables, so its behaviour is independent of any provider's resolution rules.
"""

from typing import override

import pytest

from search_api.api.beacon.models import (
    BeaconFilteringOntology,
    BeaconFilteringTerm,
    BeaconFilteringTermType,
    BeaconQueryFilter,
    OntologyRestriction,
)
from search_api.services.ontology.service import OntologyService, normalise_term

CONCEPT_ID_C1 = "C1"
CONCEPT_ID_C2 = "C2"
CONCEPT_ID_C3 = "C3"
CONCEPT_ID_C4 = "C4"
CONCEPT_IDS = {CONCEPT_ID_C1, CONCEPT_ID_C2, CONCEPT_ID_C3, CONCEPT_ID_C4}

PREFERRED_TERM_OF_C1 = "P1"
PREFERRED_TERM_OF_C2_AND_C3 = "P2"

CONCEPT_IDS_BY_TERM = {
    PREFERRED_TERM_OF_C1: {CONCEPT_ID_C1},
    PREFERRED_TERM_OF_C2_AND_C3: {CONCEPT_ID_C2, CONCEPT_ID_C3},
}

DESCENDANT_IDS_BY_CONCEPT_ID = {
    CONCEPT_ID_C1: {CONCEPT_ID_C3, CONCEPT_ID_C4},  # C1 has two descendants
    CONCEPT_ID_C2: {CONCEPT_ID_C4},  # C4 has two parents
}


def term(type: BeaconFilteringTermType = "ontology") -> BeaconFilteringTerm:
    return BeaconFilteringTerm(
        id="species",
        type=type,
        scopes=["clinical"],
        label="Species",
        description="Species",
        ontology=BeaconFilteringOntology(id="TEST"),
    )


def filter(
    value: str | list[str], include_descendants: bool = False
) -> BeaconQueryFilter:
    return BeaconQueryFilter(
        id="species", value=value, includeDescendantTerms=include_descendants
    )


class MockTermCache:
    def __init__(self, concept_ids_by_term: dict[str, set[str]] | None = None) -> None:
        self.concept_ids_by_term = concept_ids_by_term or {}
        self.calls: list[tuple[str, str]] = []

    async def get_concept_ids_by_term(self, field_id: str, term: str) -> set[str]:
        self.calls.append((field_id, term))
        return set(self.concept_ids_by_term.get(term, ()))


class MockOntologyService(OntologyService):
    """OntologyService whose resolution hooks are lookup tables.

    Records what the template asked of each hook, so the calls themselves
    can be asserted. ``get_preferred_terms`` is implemented to satisfy the
    ABC and not used in this test.
    """

    def __init__(self) -> None:
        self.find_concept_calls: list[tuple[str, BeaconFilteringTerm]] = []
        self.find_descendant_calls: list[set[str]] = []

    @override
    def is_well_formed(self, concept_id: str) -> bool:
        return concept_id.startswith("C")

    @override
    def is_concept_id_prefix(self, value: str) -> bool:
        return value.startswith("C")

    @override
    async def is_known(self, concept_id: str) -> bool:
        return concept_id in CONCEPT_IDS

    @override
    async def get_preferred_terms(self, concept_ids: set[str]) -> dict[str, str]:
        return {}

    @override
    async def _find_concept_ids(
        self, value: str, filtering_term: BeaconFilteringTerm
    ) -> set[str]:
        self.find_concept_calls.append((value, filtering_term))
        return set(CONCEPT_IDS_BY_TERM.get(value, ()))

    @override
    async def _is_within_restriction(
        self, concept_id: str, restriction: OntologyRestriction
    ) -> bool:
        return True

    @override
    async def _find_descendant_ids(self, concept_ids: set[str]) -> set[str]:
        self.find_descendant_calls.append(set(concept_ids))
        descendant_ids: set[str] = set()
        for concept_id in concept_ids:
            descendant_ids.update(DESCENDANT_IDS_BY_CONCEPT_ID.get(concept_id, ()))
        return descendant_ids


@pytest.mark.parametrize(
    "value,expected",
    [
        ("Homo sapiens", "homo sapiens"),
        ("HOMO SAPIENS", "homo sapiens"),
        ("  Homo   sapiens  ", "homo sapiens"),
        ("Homo\tsapiens", "homo sapiens"),
        ("", ""),
    ],
)
def test_normalise_term_ignores_case_and_spacing(value, expected):
    assert normalise_term(value) == expected


@pytest.fixture
def service() -> MockOntologyService:
    return MockOntologyService()


@pytest.mark.asyncio
async def test_prepare_ontology_filter_preferred_term(service):
    result = await service.prepare_ontology_filter(
        filter(PREFERRED_TERM_OF_C1), [term()]
    )
    assert result.value == [CONCEPT_ID_C1]


@pytest.mark.asyncio
async def test_prepare_ontology_filter_concept_id(service):
    result = await service.prepare_ontology_filter(
        filter([CONCEPT_ID_C1, CONCEPT_ID_C2]), [term()]
    )
    assert set(result.value) == {CONCEPT_ID_C1, CONCEPT_ID_C2}
    assert service.find_concept_calls == []


@pytest.mark.asyncio
async def test_prepare_ontology_filter_shared_preferred_term(service):
    result = await service.prepare_ontology_filter(
        filter(PREFERRED_TERM_OF_C2_AND_C3), [term()]
    )
    assert set(result.value) == {CONCEPT_ID_C2, CONCEPT_ID_C3}


@pytest.mark.asyncio
async def test_prepare_ontology_filter_two_preferred_terms(service):
    filtering_term = term()

    result = await service.prepare_ontology_filter(
        filter([PREFERRED_TERM_OF_C1, PREFERRED_TERM_OF_C2_AND_C3]), [filtering_term]
    )

    assert sorted(value for value, _ in service.find_concept_calls) == [
        PREFERRED_TERM_OF_C1,
        PREFERRED_TERM_OF_C2_AND_C3,
    ]
    assert all(t is filtering_term for _, t in service.find_concept_calls)
    assert set(result.value) == {CONCEPT_ID_C1, CONCEPT_ID_C2, CONCEPT_ID_C3}


@pytest.mark.asyncio
async def test_prepare_ontology_filter_cached(
    service,
):
    term_cache = MockTermCache({PREFERRED_TERM_OF_C1: {CONCEPT_ID_C1}})

    result = await service.prepare_ontology_filter(
        filter(PREFERRED_TERM_OF_C1), [term()], term_cache
    )

    assert result.value == [CONCEPT_ID_C1]
    assert service.find_concept_calls == []
    assert term_cache.calls == [("species", PREFERRED_TERM_OF_C1)]


@pytest.mark.asyncio
async def test_prepare_ontology_filter_not_cached(service):
    term_cache = MockTermCache()

    result = await service.prepare_ontology_filter(
        filter(PREFERRED_TERM_OF_C2_AND_C3), [term()], term_cache
    )

    assert set(result.value) == {CONCEPT_ID_C2, CONCEPT_ID_C3}
    assert [value for value, _ in service.find_concept_calls] == [
        PREFERRED_TERM_OF_C2_AND_C3
    ]


@pytest.mark.asyncio
async def test_prepare_ontology_filter_ontology_drops_unresolved_values(service):
    result = await service.prepare_ontology_filter(
        filter([CONCEPT_ID_C1, "invalid"]), [term()]
    )
    assert result.value == [CONCEPT_ID_C1]

    result = await service.prepare_ontology_filter(
        filter(["invalid1", "invalid2"]), [term()]
    )
    assert result.value == []


@pytest.mark.asyncio
async def test_prepare_ontology_filter_ontology_or_value_keeps_unresolved_values(
    service,
):
    result = await service.prepare_ontology_filter(
        filter([CONCEPT_ID_C1, "invalid"]), [term("ontologyOrValue")]
    )
    assert set(result.value) == {CONCEPT_ID_C1, "invalid"}

    result = await service.prepare_ontology_filter(
        filter(["invalid1", "invalid2"]), [term("ontologyOrValue")]
    )
    assert set(result.value) == {"invalid1", "invalid2"}


@pytest.mark.asyncio
async def test_prepare_ontology_filter_false_include_descendants(service):
    result = await service.prepare_ontology_filter(
        filter(CONCEPT_ID_C1, include_descendants=False), [term()]
    )
    assert result.value == [CONCEPT_ID_C1]
    assert service.find_descendant_calls == []


@pytest.mark.asyncio
async def test_prepare_ontology_filter_true_include_descendants(service):
    result = await service.prepare_ontology_filter(
        filter([CONCEPT_ID_C1, CONCEPT_ID_C2], include_descendants=True), [term()]
    )
    assert set(result.value) == {
        CONCEPT_ID_C1,
        CONCEPT_ID_C2,
        CONCEPT_ID_C3,
        CONCEPT_ID_C4,
    }
    # One call with every resolved concept id, not one call per value.
    assert service.find_descendant_calls == [{CONCEPT_ID_C1, CONCEPT_ID_C2}]


@pytest.mark.asyncio
async def test_prepare_ontology_filter_concept_ids_are_deduplicated(service):
    """C3 is resolved directly and is also a descendant of C1; C4 is a
    descendant of both C1 and C3's siblings. Each appears once."""
    result = await service.prepare_ontology_filter(
        filter([CONCEPT_ID_C1, CONCEPT_ID_C3], include_descendants=True), [term()]
    )
    assert sorted(result.value) == [CONCEPT_ID_C1, CONCEPT_ID_C3, CONCEPT_ID_C4]
