import os
import uuid

import pytest
import pytest_asyncio

from search_api.database.models import StoredTerm
from search_api.database.repository import get_cursor
from search_api.database.terms_cache import (
    TERMS_CACHE_TABLE,
    insert_terms,
    read_concept_ids_by_field,
    read_terms,
    read_updated_at,
    update_terms,
)

os.environ.setdefault("POSTGRES_DB", os.environ["BP_POSTGRES_DB"])
os.environ.setdefault("POSTGRES_PORT", os.environ["BP_POSTGRES_PORT"])


def _term(field_id: str, concept_id: str, preferred_term: str) -> StoredTerm:
    return StoredTerm(
        field_id=field_id, concept_id=concept_id, preferred_term=preferred_term
    )


@pytest_asyncio.fixture
async def ontology_id():
    """An ontology id of its own, deleted afterwards, so tests cannot collide."""
    ontology_id = f"TEST-{uuid.uuid4()}"
    yield ontology_id
    async with get_cursor() as cur:
        await cur.execute(
            f"DELETE FROM {TERMS_CACHE_TABLE} WHERE ontology_id = %s", (ontology_id,)
        )


@pytest.mark.asyncio
async def test_insert_and_read_terms(ontology_id):
    terms = [_term("f1", "c1", "P1"), _term("f2", "c2", "P2")]

    await insert_terms(ontology_id, terms)

    assert sorted(await read_terms(ontology_id), key=lambda t: t.concept_id) == terms


@pytest.mark.asyncio
async def test_read_terms_unknown_ontology(ontology_id):
    assert await read_terms(ontology_id) == []


@pytest.mark.asyncio
async def test_insert_terms_keeps_what_is_already_stored(ontology_id):
    """A concept already cached keeps its term."""
    await insert_terms(ontology_id, [_term("f1", "c1", "P1")])
    await insert_terms(ontology_id, [_term("f1", "c1", "P1 changed")])
    assert await read_terms(ontology_id) == [_term("f1", "c1", "P1")]


@pytest.mark.asyncio
async def test_update_terms_replaces_only_the_term_it_names(ontology_id):
    await insert_terms(
        ontology_id, [_term("f1", "c1", "P1"), _term("f2", "c1", "other")]
    )

    await update_terms(ontology_id, [_term("f1", "c1", "P1 renamed")])

    assert sorted(await read_terms(ontology_id), key=lambda t: t.field_id) == [
        _term("f1", "c1", "P1 renamed"),
        _term("f2", "c1", "other"),
    ]


@pytest.mark.asyncio
async def test_read_concept_ids_by_field(ontology_id):
    await insert_terms(
        ontology_id,
        [_term("f1", "c1", "P1"), _term("f1", "c2", "P2"), _term("f2", "c3", "P3")],
    )

    assert await read_concept_ids_by_field(ontology_id) == {
        "f1": {"c1", "c2"},
        "f2": {"c3"},
    }


@pytest.mark.asyncio
async def test_read_updated_at_unknown_ontology(ontology_id):
    assert await read_updated_at(ontology_id) is None


@pytest.mark.asyncio
async def test_read_updated_at_changes_on_insert(ontology_id):
    await insert_terms(ontology_id, [_term("f1", "c1", "P1")])
    updated_at = await read_updated_at(ontology_id)

    await insert_terms(ontology_id, [_term("f1", "c2", "P2")])

    assert await read_updated_at(ontology_id) > updated_at


@pytest.mark.asyncio
async def test_read_updated_at_changes_on_update(ontology_id):
    """An update has to move updated_at, or a rename never reaches a reader."""
    await insert_terms(ontology_id, [_term("f1", "c1", "P1")])
    updated_at = await read_updated_at(ontology_id)

    await update_terms(ontology_id, [_term("f1", "c1", "P1 renamed")])

    assert await read_updated_at(ontology_id) > updated_at
