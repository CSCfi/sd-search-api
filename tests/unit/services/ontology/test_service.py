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
)
from search_api.services.ontology.service import OntologyService, normalise_term

# Which concept id(s) each term resolves to and the concept id
# parent/child graph. By convention, concept ids start with C
# and preferred terms start with P.
CONCEPT_IDS_BY_VALUE = {
    "C1": {"C1"},
    "C2": {"C2"},
    "C3": {"C3"},
    "P1": {"C1"},
    "P2": {"C2", "C3"},  # a term resolving to > 1 concept ids (e.g. in SEND)
}

DESCENDANT_IDS_BY_CONCEPT_ID = {
    "C1": {"C3", "C4"},  # C1 has two descendants
    "C2": {"C4"},  # C4 has two parents
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
    def is_concept_id(self, value: str) -> bool:
        return value.startswith("C")

    @override
    async def get_preferred_terms(self, concept_ids: set[str]) -> dict[str, str]:
        return {}

    @override
    async def _find_concept_ids(
        self, value: str, filtering_term: BeaconFilteringTerm
    ) -> set[str]:
        self.find_concept_calls.append((value, filtering_term))
        return set(CONCEPT_IDS_BY_VALUE.get(value, ()))

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
async def test_resolves_a_scalar_value(service):
    result = await service.prepare_ontology_filter(filter("P1"), [term()])
    assert result.value == ["C1"]


@pytest.mark.asyncio
async def test_resolves_a_list_of_values(service):
    result = await service.prepare_ontology_filter(filter(["C1", "C2"]), [term()])
    assert set(result.value) == {"C1", "C2"}


@pytest.mark.asyncio
async def test_resolves_a_value_to_several_concept_ids(service):
    """A term that isn't unique resolves to every concept carrying it."""
    result = await service.prepare_ontology_filter(filter("P2"), [term()])
    assert set(result.value) == {"C2", "C3"}


@pytest.mark.asyncio
async def test_resolves_each_value_against_the_matched_filtering_term(service):
    """Each value is resolved once, against the filtering term it matched —
    a provider such as SNOMED reads its search hierarchy off that term."""
    filtering_term = term()

    result = await service.prepare_ontology_filter(
        filter(["P1", "P2"]), [filtering_term]
    )

    assert sorted(value for value, _ in service.find_concept_calls) == ["P1", "P2"]
    assert all(t is filtering_term for _, t in service.find_concept_calls)
    assert set(result.value) == {"C1", "C2", "C3"}


@pytest.mark.asyncio
async def test_concept_id_resolves_without_using_ontology_service(service):
    result = await service.prepare_ontology_filter(filter("C1"), [term()])

    assert result.value == ["C1"]
    assert service.find_concept_calls == []


@pytest.mark.asyncio
async def test_cached_preferred_term_resolves_without_using_ontology_service(
    service,
):
    term_cache = MockTermCache({"P1": {"C1"}})

    result = await service.prepare_ontology_filter(filter("P1"), [term()], term_cache)

    assert result.value == ["C1"]
    assert service.find_concept_calls == []
    assert term_cache.calls == [("species", "P1")]


@pytest.mark.asyncio
async def test_not_concept_id_or_cached_preferred_term_uses_ontology_service(service):
    term_cache = MockTermCache()

    result = await service.prepare_ontology_filter(filter("P2"), [term()], term_cache)

    assert set(result.value) == {"C2", "C3"}
    assert [value for value, _ in service.find_concept_calls] == ["P2"]


@pytest.mark.asyncio
async def test_unresolved_values_are_dropped_for_an_ontology_term(service):
    """A strict "ontology" field has no free-text fallback field, so values
    that don't resolve are dropped — whether or not any other value did."""
    mixed = await service.prepare_ontology_filter(filter(["C1", "invalid"]), [term()])
    assert mixed.value == ["C1"]

    none_resolved = await service.prepare_ontology_filter(
        filter(["invalid1", "invalid2"]), [term()]
    )
    assert none_resolved.value == []


@pytest.mark.asyncio
async def test_unresolved_values_are_kept_for_an_ontology_or_value_term(service):
    """An "ontologyOrValue" field also queries a free-text fallback field, so
    values that don't resolve are kept for it."""
    mixed = await service.prepare_ontology_filter(
        filter(["C1", "invalid"]), [term("ontologyOrValue")]
    )
    assert set(mixed.value) == {"C1", "invalid"}

    none_resolved = await service.prepare_ontology_filter(
        filter(["invalid1", "invalid2"]), [term("ontologyOrValue")]
    )
    assert set(none_resolved.value) == {"invalid1", "invalid2"}


@pytest.mark.asyncio
async def test_descendants_are_not_resolved_unless_requested(service):
    result = await service.prepare_ontology_filter(
        filter("C1", include_descendants=False), [term()]
    )
    assert result.value == ["C1"]
    assert service.find_descendant_calls == []


@pytest.mark.asyncio
async def test_descendants_are_resolved_once_for_all_concept_ids(service):
    result = await service.prepare_ontology_filter(
        filter(["C1", "C2"], include_descendants=True), [term()]
    )
    assert set(result.value) == {"C1", "C2", "C3", "C4"}
    # One call with every resolved concept id, not one call per value.
    assert service.find_descendant_calls == [{"C1", "C2"}]


@pytest.mark.asyncio
async def test_concept_ids_are_deduplicated(service):
    """C3 is resolved directly and is also a descendant of C1; C4 is a
    descendant of both C1 and C3's siblings. Each appears once."""
    result = await service.prepare_ontology_filter(
        filter(["C1", "C3"], include_descendants=True), [term()]
    )
    assert sorted(result.value) == ["C1", "C3", "C4"]
