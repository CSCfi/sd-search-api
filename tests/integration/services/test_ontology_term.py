import os
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from search_api.api.beacon.models import SNOMED_ONTOLOGY_ID
from search_api.database.repository import get_connection
from search_api.services.snomed import SnomedService
from search_api.services.ontology_term import (
    TERMS_CACHE_TABLE,
    PostgresOntologyTermCacheService,
)

os.environ.setdefault("POSTGRES_DB", os.environ["BP_POSTGRES_DB"])
os.environ.setdefault("POSTGRES_PORT", os.environ["BP_POSTGRES_PORT"])

_FIELD_ID = "animal_species"


def _service() -> PostgresOntologyTermCacheService:
    return PostgresOntologyTermCacheService(ontology_id=SNOMED_ONTOLOGY_ID)


async def _get_stored_term(concept_id: str, field_id: str = _FIELD_ID) -> str | None:
    """Return the preferred_term stored in concept for (concept_id, field_id), or None."""
    async with get_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                f"SELECT preferred_term FROM {TERMS_CACHE_TABLE} "
                "WHERE ontology_id = %s AND concept_id = %s AND field_id = %s",
                (SNOMED_ONTOLOGY_ID, concept_id, field_id),
            )
            row = await cur.fetchone()
    return row[0] if row else None


CACHED_TERMS = {
    "337915000": "Homo sapiens",
    "80248007": "Breast structure",
    "119376003": "Tissue specimen",
}


@pytest_asyncio.fixture(autouse=True)
async def _clear_cache():
    concept_ids = list(CACHED_TERMS.keys())

    async def _delete() -> None:
        async with get_connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    f"DELETE FROM {TERMS_CACHE_TABLE} WHERE concept_id = ANY(%s)",
                    (concept_ids,),
                )

    await _delete()
    yield
    await _delete()


@pytest_asyncio.fixture
async def fill_cache() -> PostgresOntologyTermCacheService:
    async with get_connection() as conn:
        async with conn.cursor() as cur:
            await cur.executemany(
                f"INSERT INTO {TERMS_CACHE_TABLE} "
                "(ontology_id, concept_id, field_id, preferred_term, updated_at) "
                "VALUES (%s, %s, %s, %s, now())",
                [
                    (SNOMED_ONTOLOGY_ID, cid, _FIELD_ID, term)
                    for cid, term in CACHED_TERMS.items()
                ],
            )
    service = _service()
    await service.load()
    return service


@pytest.mark.asyncio
async def test_load():
    async with get_connection() as conn:
        async with conn.cursor() as cur:
            await cur.executemany(
                f"INSERT INTO {TERMS_CACHE_TABLE} "
                "(ontology_id, concept_id, field_id, preferred_term, updated_at) "
                "VALUES (%s, %s, %s, %s, now())",
                [
                    (SNOMED_ONTOLOGY_ID, cid, _FIELD_ID, term)
                    for cid, term in CACHED_TERMS.items()
                ],
            )

    service = _service()
    assert await service.get_preferred_terms(_FIELD_ID, set(CACHED_TERMS)) == {}

    await service.load()

    assert (
        await service.get_preferred_terms(_FIELD_ID, set(CACHED_TERMS)) == CACHED_TERMS
    )
    # load() also builds the by-term direction used to resolve filter values.
    assert await service.get_concept_ids_by_term(_FIELD_ID, "Homo sapiens") == {
        "337915000"
    }


@pytest.mark.asyncio
async def test_load_does_not_raise_on_empty_table():
    """load() completes without error even when concept has no rows for these IDs."""
    service = _service()
    await service.load()
    assert await service.get_preferred_terms(_FIELD_ID, set(CACHED_TERMS)) == {}


@pytest.mark.asyncio
async def test_get_preferred_terms_returns_cached(fill_cache):
    result = await fill_cache.get_preferred_terms(_FIELD_ID, set(CACHED_TERMS.keys()))
    assert result == CACHED_TERMS


@pytest.mark.asyncio
async def test_get_preferred_terms_unknown_ids_omitted(fill_cache):
    known = "337915000"
    result = await fill_cache.get_preferred_terms(_FIELD_ID, {known, "999999999"})
    assert result == {known: CACHED_TERMS[known]}
    assert "999999999" not in result


@pytest.mark.asyncio
async def test_get_preferred_terms_empty_set(fill_cache):
    assert await fill_cache.get_preferred_terms(_FIELD_ID, set()) == {}


@pytest.mark.asyncio
async def test_cache_preferred_terms_skips_existing_ids(fill_cache):
    mock_snomed = AsyncMock()
    mock_snomed.get_preferred_terms = AsyncMock(return_value={})

    await fill_cache.cache_preferred_terms(
        _FIELD_ID, set(CACHED_TERMS.keys()), mock_snomed
    )

    mock_snomed.get_preferred_terms.assert_not_called()


@pytest.mark.asyncio
async def test_cache_preferred_terms_stores_new_terms():
    service = _service()
    await service.load()

    mock_snomed = AsyncMock()
    mock_snomed.get_preferred_terms = AsyncMock(
        return_value={"337915000": "Homo sapiens"}
    )

    await service.cache_preferred_terms(_FIELD_ID, {"337915000"}, mock_snomed)

    assert await service.get_preferred_terms(_FIELD_ID, {"337915000"}) == {
        "337915000": "Homo sapiens"
    }
    assert await _get_stored_term("337915000") == "Homo sapiens"


@pytest.mark.asyncio
async def test_cache_preferred_terms_skip_when_snowstorm_returns_empty():
    service = _service()
    await service.load()

    mock_snomed = AsyncMock()
    mock_snomed.get_preferred_terms = AsyncMock(return_value={})

    await service.cache_preferred_terms(_FIELD_ID, {"337915000"}, mock_snomed)

    assert await service.get_preferred_terms(_FIELD_ID, {"337915000"}) == {}
    assert await _get_stored_term("337915000") is None


@pytest.mark.asyncio
async def test_cache_preferred_terms_resolves_via_snowstorm():
    service = _service()
    await service.load()

    await service.cache_preferred_terms(_FIELD_ID, {"337915000"}, SnomedService())

    result = await service.get_preferred_terms(_FIELD_ID, {"337915000"})
    assert result.get("337915000", "").lower().startswith("homo")

    stored = await _get_stored_term("337915000")
    assert stored is not None
    assert stored.lower().startswith("homo")


@pytest.mark.asyncio
async def test_has_changes_since():
    initial_term_cnt = 500
    extra_term_cnt = 50
    initial_terms = {str(uuid.uuid4()): f"Term {i}" for i in range(initial_term_cnt)}
    extra_terms = {str(uuid.uuid4()): f"Term {i}" for i in range(extra_term_cnt)}
    all_concept_ids = list(initial_terms) + list(extra_terms)

    initial_ts = datetime.now(timezone.utc) - timedelta(seconds=10)

    try:
        async with get_connection() as conn:
            async with conn.cursor() as cur:
                await cur.executemany(
                    f"INSERT INTO {TERMS_CACHE_TABLE} "
                    "(ontology_id, concept_id, field_id, preferred_term, updated_at) "
                    "VALUES (%s, %s, %s, %s, %s)",
                    [
                        (SNOMED_ONTOLOGY_ID, concept_id, _FIELD_ID, term, initial_ts)
                        for concept_id, term in initial_terms.items()
                    ],
                )

        service = _service()
        await service.load()

        assert service._last_refreshed is not None
        assert (
            await service.get_preferred_terms(_FIELD_ID, set(initial_terms))
            == initial_terms
        )

        current_ts = datetime.now(timezone.utc)

        # No new rows
        assert not await service._has_changes_since(current_ts)

        future_ts = current_ts + timedelta(seconds=10)
        async with get_connection() as conn:
            async with conn.cursor() as cur:
                await cur.executemany(
                    f"INSERT INTO {TERMS_CACHE_TABLE} "
                    "(ontology_id, concept_id, field_id, preferred_term, updated_at) "
                    "VALUES (%s, %s, %s, %s, %s)",
                    [
                        (SNOMED_ONTOLOGY_ID, concept_id, _FIELD_ID, term, future_ts)
                        for concept_id, term in extra_terms.items()
                    ],
                )

        # Extra rows exist
        assert await service._has_changes_since(current_ts)

        # Loads extra rows
        await service.load()
        assert (
            await service.get_preferred_terms(
                _FIELD_ID, set(initial_terms) | set(extra_terms)
            )
            == initial_terms | extra_terms
        )
    finally:
        async with get_connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    f"DELETE FROM {TERMS_CACHE_TABLE} WHERE concept_id = ANY(%s)",
                    (all_concept_ids,),
                )
