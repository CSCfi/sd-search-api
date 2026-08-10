import asyncio
from collections.abc import Sequence
from datetime import datetime, timezone

import pytest

from search_api.api.beacon.models import (
    BeaconFilteringOntology,
    BeaconFilteringTerm,
    OntologyRestriction,
)
from search_api.services.ontology.cache.models import (
    CachedOntology,
    CachedOntologyConcept,
)
from search_api.services.ontology.cache.service import CachedOntologyService
from search_api.services.ontology.cache.source import OntologySource

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


def cached_ontology(
    concepts: list[CachedOntologyConcept], version: str = "v1", sha256: str = "hash1"
) -> CachedOntology:
    return CachedOntology(version=version, sha256=sha256, concepts=concepts)


class MockSource(OntologySource):
    """Serves one ontology, and reports whatever change a test gives it."""

    def __init__(
        self, fetched: CachedOntology, updated_at: datetime | None = None
    ) -> None:
        self.fetched = fetched
        self.changed_at = updated_at
        self.fetch_count = 0

    async def fetch(self) -> CachedOntology:
        self.fetch_count += 1
        return self.fetched

    def is_newer(self, version: str, other: str) -> bool:
        return version > other

    async def updated_at(self) -> datetime | None:
        return self.changed_at


class MockStore:
    """Stands in for the ontology_cache table."""

    def __init__(self) -> None:
        self.stored: CachedOntology | None = None
        self.write_count = 0
        self.read_count = 0
        self.stored_at: datetime | None = None

    async def read(self) -> CachedOntology | None:
        self.read_count += 1
        return self.stored

    async def write(self, fetched: CachedOntology) -> None:
        self.write_count += 1
        self.stored = fetched
        self.stored_at = datetime(2026, 1, 1, tzinfo=timezone.utc)

    async def updated_at(self) -> datetime | None:
        return self.stored_at


class FailingMockSource(OntologySource):
    async def fetch(self) -> CachedOntology:
        raise ConnectionError("unreachable")

    def is_newer(self, version: str, other: str) -> bool:
        return version > other


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
    """A service over an empty store, so init fills it from the source."""
    return CachedOntologyService(MockStore(), MockSource(cached_ontology(concepts)))


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
async def test_find_concept_ids_for_term_shared_by_several_concepts():
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

    service._set_concepts(cached_ontology(V2_CONCEPTS, version="v2"))

    assert not service.is_concept_id("C4")
    assert service.is_concept_id("C7")


@pytest.mark.asyncio
async def test_init_serves_what_is_stored_without_fetching():
    store = MockStore()
    await store.write(cached_ontology(V1_CONCEPTS))
    source = MockSource(cached_ontology(V2_CONCEPTS, version="v2"))
    service = CachedOntologyService(store, source)

    await service.init()

    assert service.is_concept_id("C1")
    assert source.fetch_count == 0
    assert store.write_count == 1  # only the test's own write


@pytest.mark.asyncio
async def test_init_fetches_and_stores_when_nothing_is_stored():
    store = MockStore()
    source = MockSource(cached_ontology(V1_CONCEPTS))
    service = CachedOntologyService(store, source)

    await service.init()

    assert service.is_concept_id("C1")
    assert source.fetch_count == 1
    assert store.stored == source.fetched


@pytest.mark.asyncio
async def test_init_propagates_a_fetch_failure_when_nothing_is_stored():
    service = CachedOntologyService(MockStore(), FailingMockSource())

    with pytest.raises(ConnectionError):
        await service.init()


@pytest.mark.asyncio
async def test_reloads_when_another_process_writes_the_store():
    store = MockStore()
    await store.write(cached_ontology(V1_CONCEPTS))
    service = CachedOntologyService(
        store, MockSource(cached_ontology(V1_CONCEPTS)), refresh_interval=0.01
    )
    await service.init()
    assert service.is_concept_id("C1")

    # Another process replaced the stored ontology.
    store.stored = cached_ontology(V2_CONCEPTS, version="v2", sha256="hash2")
    store.stored_at = datetime(2026, 2, 1, tzinfo=timezone.utc)

    await service.start()
    try:
        await asyncio.sleep(0.05)
    finally:
        service.stop()

    assert service.is_concept_id("C6")
    assert not service.is_concept_id("C1")


@pytest.mark.asyncio
async def test_does_not_reload_while_the_store_is_unchanged():
    store = MockStore()
    await store.write(cached_ontology(V1_CONCEPTS))
    service = CachedOntologyService(
        store, MockSource(cached_ontology(V1_CONCEPTS)), refresh_interval=0.01
    )

    await service.start()
    reads = store.read_count

    try:
        await asyncio.sleep(0.05)
    finally:
        service.stop()

    assert store.read_count == reads


@pytest.mark.asyncio
async def test_stop_refresh():
    service = make_service(V1_CONCEPTS)
    await service.start()
    task = service._poller._task
    service.stop()
    await asyncio.sleep(0)

    assert task is not None and task.done()
    assert service._poller._task is None
