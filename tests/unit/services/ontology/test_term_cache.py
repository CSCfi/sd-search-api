import pytest

from search_api.services.ontology.term_cache import OntologyTermCacheService

FIELD_ID = "species"
ONTOLOGY_ID = "TEST"


@pytest.fixture
def cache() -> OntologyTermCacheService:
    return OntologyTermCacheService("TEST")


@pytest.mark.asyncio
async def test_index_term_indexes_both_directions(cache):
    cache._index_term(FIELD_ID, "C1", "P1")
    cache._index_term(FIELD_ID, "C2", "P1")  # a term shared by two concepts
    cache._index_term("other_field", "C3", "P1")  # fields are indexed separately

    assert await cache.get_terms_by_concept_id(FIELD_ID, {"C1", "C2"}) == {
        "C1": "P1",
        "C2": "P1",
    }
    assert await cache.get_concept_ids_by_term(FIELD_ID, "P1") == {"C1", "C2"}
    assert await cache.get_concept_ids_by_term("other_field", "P1") == {"C3"}

    # Matched case- and space-insensitively.
    assert await cache.get_concept_ids_by_term(FIELD_ID, "  p1 ") == {"C1", "C2"}

    # An unknown field or term resolves to nothing.
    assert await cache.get_concept_ids_by_term(FIELD_ID, "missing") == set()
    assert await cache.get_concept_ids_by_term("missing_field", "P1") == set()


@pytest.mark.asyncio
async def test_re_indexing_concept_id_replaces_its_preferred_term(cache):
    """A concept id has one preferred term, so one renamed by ``refresh`` must
    stop resolving rather than linger alongside the new one."""
    cache._index_term(FIELD_ID, "C1", "P1")
    cache._index_term(FIELD_ID, "C2", "P1")
    cache._index_term(FIELD_ID, "C1", "P2")

    assert await cache.get_terms_by_concept_id(FIELD_ID, {"C1"}) == {"C1": "P2"}
    assert await cache.get_concept_ids_by_term(FIELD_ID, "P2") == {"C1"}
    assert await cache.get_concept_ids_by_term(FIELD_ID, "P1") == {"C2"}
