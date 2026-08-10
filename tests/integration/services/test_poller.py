import asyncio
import os
import uuid

import pytest
import pytest_asyncio

from search_api.database.models import StoredTerm
from search_api.database.repository import get_cursor
from search_api.database.terms_cache import TERMS_CACHE_TABLE, insert_terms
from search_api.services.ontology.term_cache import OntologyTermCache

os.environ.setdefault("POSTGRES_DB", os.environ["BP_POSTGRES_DB"])
os.environ.setdefault("POSTGRES_PORT", os.environ["BP_POSTGRES_PORT"])

_FIELD_ID = "animal_species"

_POLL_INTERVAL = 0.05
_RELOAD_TIMEOUT = 5.0


@pytest_asyncio.fixture
async def ontology_id():
    """An ontology id of its own, deleted afterwards."""
    ontology_id = f"TEST-{uuid.uuid4()}"
    yield ontology_id
    async with get_cursor() as cur:
        await cur.execute(
            f"DELETE FROM {TERMS_CACHE_TABLE} WHERE ontology_id = %s", (ontology_id,)
        )


@pytest.mark.asyncio
async def test_cache_loads(ontology_id):
    await insert_terms(
        ontology_id,
        [StoredTerm(field_id=_FIELD_ID, concept_id="C1", preferred_term="P1")],
    )
    cache = OntologyTermCache(ontology_id, refresh_interval=_POLL_INTERVAL)

    # Loaded by start(), which then keeps polling.
    await cache.start()
    try:
        assert await cache.get_terms_by_concept_id(_FIELD_ID, {"C1"}) == {"C1": "P1"}

        # Insert term.
        await insert_terms(
            ontology_id,
            [StoredTerm(field_id=_FIELD_ID, concept_id="C2", preferred_term="P2")],
        )

        # The reload happens when OntologyTermCache polls and detects that
        # the term cache store has been updated.
        waited = 0.0
        while not await cache.get_terms_by_concept_id(_FIELD_ID, {"C2"}):
            assert waited < _RELOAD_TIMEOUT, "the cache never reloaded"
            await asyncio.sleep(_POLL_INTERVAL)
            waited += _POLL_INTERVAL

        assert await cache.get_terms_by_concept_id(_FIELD_ID, {"C2"}) == {"C2": "P2"}
        assert await cache.get_concept_ids_by_term(_FIELD_ID, "P2") == {"C2"}
    finally:
        cache.stop()
