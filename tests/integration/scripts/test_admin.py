import os
import uuid

import pytest
import pytest_asyncio

from scripts.admin import _update_cached_ontology
from search_api.database.ontology_cache import ONTOLOGY_CACHE_TABLE
from search_api.database.repository import get_cursor
from search_api.services.ontology.cache.models import (
    CachedOntology,
    CachedOntologyConcept,
)
from search_api.services.ontology.cache.source import OntologySource
from search_api.services.ontology.cache.store import OntologyCacheStore

os.environ.setdefault("POSTGRES_DB", os.environ["BP_POSTGRES_DB"])
os.environ.setdefault("POSTGRES_PORT", os.environ["BP_POSTGRES_PORT"])


class _MockSource(OntologySource):
    """Serves one ontology, and compares versions as dates like a real source."""

    def __init__(self, ontology: CachedOntology) -> None:
        self._ontology = ontology

    async def fetch(self) -> CachedOntology:
        return self._ontology

    def is_newer(self, version: str, other: str) -> bool:
        return version > other


def _ontology(version: str, sha256: str, concept_id: str = "C1") -> CachedOntology:
    return CachedOntology(
        version=version,
        sha256=sha256,
        concepts=[CachedOntologyConcept(concept_id=concept_id, preferred_term="P1")],
    )


@pytest_asyncio.fixture
async def ontology_id():
    """An ontology id of its own, emptied afterwards, so tests cannot collide."""
    ontology_id = f"TEST-{uuid.uuid4()}"
    yield ontology_id
    async with get_cursor() as cur:
        await cur.execute(
            f"DELETE FROM {ONTOLOGY_CACHE_TABLE} WHERE ontology_id = %s",
            (ontology_id,),
        )


@pytest.fixture
def store(ontology_id: str) -> OntologyCacheStore:
    return OntologyCacheStore(ontology_id)


async def _update(ontology_id: str, fetched: CachedOntology) -> None:
    """Run the command against a source serving ``fetched``."""
    await _update_cached_ontology(ontology_id, _MockSource(fetched))


@pytest.mark.asyncio
async def test_update_cached_ontology_stores_fetched_ontology_when_nothing_is_stored(
    ontology_id, store
):
    fetched = _ontology("2026-01-01", "hash1")

    await _update(ontology_id, fetched)

    assert await store.read() == fetched


@pytest.mark.asyncio
async def test_update_cached_ontology_ignores_older_version(ontology_id, store):
    await store.write(_ontology("2026-02-01", "newer"))

    await _update(ontology_id, _ontology("2026-01-01", "older"))

    stored = await store.read()
    assert stored is not None
    assert stored.version == "2026-02-01"
    assert stored.sha256 == "newer"


@pytest.mark.asyncio
async def test_update_cached_ontology_replaces_ontology_when_sha256_changes(
    ontology_id, store
):
    await store.write(_ontology("2026-01-01", "hash1", concept_id="C1"))
    fetched = _ontology("2026-02-01", "hash2", concept_id="C2")

    await _update(ontology_id, fetched)

    assert await store.read() == fetched


@pytest.mark.asyncio
async def test_update_cached_ontology_changes_version_when_content_unchanged(
    ontology_id, store
):
    await store.write(_ontology("2026-01-01", "same"))

    await _update(ontology_id, _ontology("2026-02-01", "same"))

    stored = await store.read()
    assert stored is not None
    assert stored.version == "2026-02-01"
