import os
import uuid
from typing import Any

import pytest
import pytest_asyncio

from search_api.database.models import StoredOntology
from search_api.database.ontology_cache import (
    ONTOLOGY_CACHE_TABLE,
    read_ontology,
    read_updated_at,
    write_ontology,
)
from search_api.database.repository import get_cursor

os.environ.setdefault("POSTGRES_DB", os.environ["BP_POSTGRES_DB"])
os.environ.setdefault("POSTGRES_PORT", os.environ["BP_POSTGRES_PORT"])


def _concept(concept_id: str) -> dict[str, Any]:
    return {"concept_id": concept_id, "preferred_term": f"term of {concept_id}"}


@pytest_asyncio.fixture
async def ontology_id():
    """A unique ontology id of its own, deleted afterwards."""
    ontology_id = f"TEST-{uuid.uuid4()}"
    yield ontology_id
    async with get_cursor() as cur:
        await cur.execute(
            f"DELETE FROM {ONTOLOGY_CACHE_TABLE} WHERE ontology_id = %s",
            (ontology_id,),
        )


@pytest.mark.asyncio
async def test_read_unknown_ontology(ontology_id):
    assert await read_ontology(ontology_id) is None
    assert await read_updated_at(ontology_id) is None


@pytest.mark.asyncio
async def test_write_and_read_ontology(ontology_id):
    stored = StoredOntology(
        version="v1", sha256="hash1", concepts=[_concept("C1"), _concept("C2")]
    )

    await write_ontology(ontology_id, stored)

    assert await read_ontology(ontology_id) == stored
    updated_at = await read_updated_at(ontology_id)
    assert updated_at is not None

    replacement = StoredOntology(
        version="v2", sha256="hash2", concepts=[_concept("C3")]
    )

    await write_ontology(ontology_id, replacement)

    assert await read_ontology(ontology_id) == replacement
    assert await read_updated_at(ontology_id) > updated_at
