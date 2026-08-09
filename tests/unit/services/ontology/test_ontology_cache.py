"""Unit tests for CachedOntologyService hooks called by ``prepare_ontology_filter`` template method."""

from collections.abc import Sequence

import asyncio
from datetime import datetime, timezone

import pytest

from search_api.api.beacon.models import (
    BeaconFilteringOntology,
    BeaconFilteringTerm,
    OntologyRestriction,
)
from search_api.services.ontology.ontology_cache import (
    BootstrapCachedOntologySource,
    CachedOntologySource,
    CachedOntologyConcept,
    CachedOntologyService,
    CachedOntology,
)

V1_CONCEPTS = [
    CachedOntologyConcept(concept_id="C1", preferred_term="P1"),
    CachedOntologyConcept(concept_id="C2", preferred_term="P2"),
    CachedOntologyConcept(
        concept_id="C3",
        preferred_term="P3",
        synonyms=frozenset({"S1"}),
        parent_ids=frozenset({"C1"}),
    ),
    # C4 belongs to two code lists at once, like ~half of SEND's real codes.
    CachedOntologyConcept(
        concept_id="C4", preferred_term="P4", parent_ids=frozenset({"C1", "C2"})
    ),
    # C5 is a grandchild of C1, reached only by walking C3.
    CachedOntologyConcept(
        concept_id="C5", preferred_term="P5", parent_ids=frozenset({"C3"})
    ),
]

V1_ROOT_CONCEPTS: tuple[str, ...] = ("C1", "C2")

V2_CONCEPTS = [
    CachedOntologyConcept(concept_id="C6", preferred_term="P6"),
    CachedOntologyConcept(concept_id="C7", preferred_term="P7"),
]


class MockSource(CachedOntologySource):
    def __init__(self, fetched: CachedOntology) -> None:
        self._fetched = fetched
        self.fetch_count = 0

    async def fetch(self) -> CachedOntology:
        self.fetch_count += 1
        return self._fetched

    def is_newer(self, version: str, other: str) -> bool:
        return version > other


class FailingMockSource(CachedOntologySource):
    async def fetch(self) -> CachedOntology:
        raise ConnectionError("unreachable")

    def is_newer(self, version: str, other: str) -> bool:
        return version > other


class MockStore:
    def __init__(self) -> None:
        self.stored: CachedOntology | None = None
        self.write_count = 0
        self.stored_at: datetime | None = None

    async def read(self) -> CachedOntology | None:
        return self.stored

    async def write(self, fetched: CachedOntology) -> None:
        self.write_count += 1
        self.stored = fetched
        self.stored_at = datetime(2026, 1, 1, tzinfo=timezone.utc)

    async def updated_at(self) -> datetime | None:
        return self.stored_at


def term(
    restrict_concept_ids: Sequence[str] | None = V1_ROOT_CONCEPTS,
    restrict_include_descendants: bool = True,
) -> BeaconFilteringTerm:
    return BeaconFilteringTerm(
        id="species",
        type="ontology",
        scopes=["clinical"],
        label="Species",
        description="Species",
        ontology=BeaconFilteringOntology(id="TEST"),
        ontologyRestriction=(
            None
            if restrict_concept_ids is None
            else OntologyRestriction(
                concept_ids=list(restrict_concept_ids),
                include_descendants=restrict_include_descendants,
            )
        ),
    )


def make_service(concepts: list[CachedOntologyConcept]) -> CachedOntologyService:
    return CachedOntologyService(
        BootstrapCachedOntologySource(
            MockStore(),
            MockSource(CachedOntology(version="v1", sha256="hash1", concepts=concepts)),
        )
    )


@pytest.fixture
def service() -> CachedOntologyService:
    return make_service(V1_CONCEPTS)


@pytest.mark.asyncio
async def test_init(service):
    """Test that init populates the ontology cache."""
    await service.init()
    assert service.is_concept_id("C3")
    assert not service.is_concept_id("P3")


@pytest.mark.asyncio
async def test_get_preferred_terms(service):
    await service.init()
    result = await service.get_preferred_terms({"C3", "C4", "missing"})
    assert result == {"C3": "P3", "C4": "P4"}


@pytest.mark.asyncio
async def test_find_concept_ids(service):
    await service.init()

    assert await service._find_concept_ids("C3", term()) == {"C3"}
    assert await service._find_concept_ids("c3", term()) == {"C3"}
    assert await service._find_concept_ids("P3", term()) == {"C3"}
    assert await service._find_concept_ids("p3", term()) == {"C3"}
    assert await service._find_concept_ids("S1", term()) == {"C3"}
    assert await service._find_concept_ids("s1", term()) == {"C3"}
    assert await service._find_concept_ids("invalid", term()) == set()


@pytest.mark.asyncio
async def test_find_concept_ids_for_a_term_shared_by_several_concepts():
    """A preferred term or synonym isn't guaranteed unique across concepts
    (real SEND has some), so it resolves to all of them rather than dropping
    the association."""
    service = make_service(
        [
            CachedOntologyConcept(concept_id="C1", preferred_term="P1"),
            CachedOntologyConcept(concept_id="C2", preferred_term="P1"),
        ]
    )
    await service.init()

    assert await service._find_concept_ids("P1", term()) == {"C1", "C2"}


@pytest.mark.asyncio
async def test_find_concept_ids_with_restriction(service):
    await service.init()

    # C4 is a descendant of both C1 and C2, so either restriction reaches it.
    assert await service._find_concept_ids("P4", term(["C1"])) == {"C4"}
    assert await service._find_concept_ids("P4", term(["C2"])) == {"C4"}

    # C3 is a descendant of C1 only, so a C2 restriction does not reach it.
    assert await service._find_concept_ids("P3", term(["C1"])) == {"C3"}
    assert await service._find_concept_ids("P3", term(["C2"])) == set()
    assert await service._find_concept_ids("P3", term(None)) == {"C3"}

    # Excluding descendants, C1's descendant C3 no longer resolves.
    assert await service._find_concept_ids(
        "P1", term(["C1"], restrict_include_descendants=False)
    ) == {"C1"}
    assert (
        await service._find_concept_ids(
            "P3", term(["C1"], restrict_include_descendants=False)
        )
        == set()
    )


@pytest.mark.asyncio
async def test_find_descendant_ids(service):
    await service.init()

    # C1's children are C3 and C4, and C3's child C5 is reached transitively.
    assert await service._find_descendant_ids({"C1"}) == {"C3", "C4", "C5"}
    assert await service._find_descendant_ids({"C3"}) == {"C5"}
    # C4 has two parents, so it is reached from C1 above and from C2 here.
    assert await service._find_descendant_ids({"C2"}) == {"C4"}
    # Unions across concept ids, with C4 (a descendant of both C1 and C2) returned once.
    assert await service._find_descendant_ids({"C1", "C2"}) == {"C3", "C4", "C5"}
    # A leaf and an unknown concept id both resolve to nothing.
    assert await service._find_descendant_ids({"C5"}) == set()
    assert await service._find_descendant_ids({"invalid"}) == set()


@pytest.mark.asyncio
async def test_set_concepts_swaps_table_to_new_version(service):
    await service.init()
    assert service.is_concept_id("C4")

    service._set_concepts(
        CachedOntology(version="v2", sha256="hash2", concepts=V2_CONCEPTS)
    )

    assert not service.is_concept_id("C4")
    assert service.is_concept_id("C7")


@pytest.mark.asyncio
async def test_bootstrap_source_fetches_live_persists_and_does_not_rewrite_on_repeat():
    store = MockStore()
    live_fetched = CachedOntology(version="v1", sha256="hash1", concepts=V1_CONCEPTS)
    source = BootstrapCachedOntologySource(store, MockSource(live_fetched))

    fetched = await source.fetch()

    assert fetched.version == "v1"
    assert fetched.sha256 == "hash1"
    assert {c.concept_id for c in fetched.concepts} == {"C1", "C2", "C3", "C4", "C5"}
    assert store.write_count == 1
    assert store.stored == live_fetched

    # A second fetch reads what was stored rather than rewriting it.
    fetched = await source.fetch()
    assert fetched == live_fetched
    assert store.write_count == 1


@pytest.mark.asyncio
async def test_bootstrap_source_prefers_stored_data_without_touching_live_source():
    store = MockStore()
    store.stored = CachedOntology(version="v1", sha256="hash1", concepts=V2_CONCEPTS)
    live = MockSource(
        CachedOntology(version="v2", sha256="hash2", concepts=V1_CONCEPTS)
    )

    fetched = await BootstrapCachedOntologySource(store, live).fetch()

    assert fetched.version == "v1"
    assert fetched.sha256 == "hash1"
    assert {c.concept_id for c in fetched.concepts} == {"C6", "C7"}
    assert live.fetch_count == 0
    assert store.write_count == 0


@pytest.mark.asyncio
async def test_bootstrap_source_propagates_live_failure_when_store_is_empty():
    store = MockStore()
    source = BootstrapCachedOntologySource(store, FailingMockSource())

    with pytest.raises(ConnectionError):
        await source.fetch()


# Refresh ontology cache.
#


def _cached_ontology_v1() -> CachedOntology:
    return CachedOntology(version="2026-01-01", sha256="a", concepts=V1_CONCEPTS)


def _cached_ontology_v2() -> CachedOntology:
    return CachedOntology(version="2026-02-01", sha256="b", concepts=V2_CONCEPTS)


def _service(store: MockStore) -> CachedOntologyService:
    source = BootstrapCachedOntologySource(store, MockSource(_cached_ontology_v1()))
    return CachedOntologyService(source, refresh_interval=0.01)


@pytest.mark.asyncio
async def test_refresh_cache():
    store = MockStore()
    await store.write(_cached_ontology_v1())
    service = _service(store)
    await service.init()
    assert service.is_concept_id("C1")

    # Ontology changed in mock store.
    store.stored = _cached_ontology_v2()
    store.stored_at = datetime(2026, 2, 1, tzinfo=timezone.utc)

    service.start()
    try:
        await asyncio.sleep(0.05)
    finally:
        service.stop()

    assert service.is_concept_id("C6")
    assert not service.is_concept_id("C1")


@pytest.mark.asyncio
async def test_stop_refresh():
    service = _service(MockStore())
    service.start()
    task = service._task
    service.stop()
    await asyncio.sleep(0)

    assert task is not None and task.done()
    assert service._task is None
