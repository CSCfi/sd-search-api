import os
from uuid import uuid4

import pytest

from search_api.database.repository import get_connection
from search_api.database.ontology_cache import ONTOLOGY_CACHE_TABLE
from search_api.services.ontology.ontology_cache import (
    DatabaseOntologyStore,
    CachedOntologyConcept,
    CachedOntology,
)

os.environ.setdefault("POSTGRES_DB", os.environ["BP_POSTGRES_DB"])
os.environ.setdefault("POSTGRES_PORT", os.environ["BP_POSTGRES_PORT"])


@pytest.mark.asyncio
async def test_read_write_replace_and_ontology_isolation():
    concepts = [
        CachedOntologyConcept(concept_id="C1", preferred_term="P1"),
        CachedOntologyConcept(
            concept_id="C2",
            preferred_term="P2",
            synonyms=frozenset({"S1"}),
            parent_ids=frozenset({"C1"}),
        ),
    ]
    other_concepts = [CachedOntologyConcept(concept_id="C3", preferred_term="P3")]
    ontology_id = str(uuid4())
    other_ontology_id = str(uuid4())
    store = DatabaseOntologyStore(ontology_id)
    other_store = DatabaseOntologyStore(other_ontology_id)

    try:
        assert await store.read() is None

        await store.write(
            CachedOntology(version="v1", sha256="hash1", concepts=concepts)
        )
        await other_store.write(
            CachedOntology(version="v1", sha256="hash2", concepts=other_concepts)
        )

        fetched = await store.read()
        assert fetched.version == "v1"
        assert fetched.sha256 == "hash1"
        by_id = {c.concept_id: c for c in fetched.concepts}
        assert set(by_id) == {"C1", "C2"}
        assert by_id["C1"].preferred_term == "P1"
        assert by_id["C1"].synonyms == frozenset()
        assert by_id["C1"].parent_ids == frozenset()
        assert by_id["C2"].preferred_term == "P2"
        assert by_id["C2"].synonyms == frozenset({"S1"})
        assert by_id["C2"].parent_ids == frozenset({"C1"})

        other_fetched = await other_store.read()
        assert other_fetched.version == "v1"
        assert other_fetched.sha256 == "hash2"
        other_by_id = {c.concept_id: c for c in other_fetched.concepts}
        assert set(other_by_id) == {"C3"}
        assert other_by_id["C3"].preferred_term == "P3"
        assert other_by_id["C3"].synonyms == frozenset()
        assert other_by_id["C3"].parent_ids == frozenset()

        new_concepts = [CachedOntologyConcept(concept_id="C4", preferred_term="P4")]
        await store.write(
            CachedOntology(version="v2", sha256="hash3", concepts=new_concepts)
        )

        fetched = await store.read()
        assert fetched.version == "v2"
        assert fetched.sha256 == "hash3"
        by_id = {c.concept_id: c for c in fetched.concepts}
        assert set(by_id) == {"C4"}
        assert by_id["C4"].preferred_term == "P4"
        assert by_id["C4"].synonyms == frozenset()
        assert by_id["C4"].parent_ids == frozenset()

        # Other_store remains unchanged.
        other_fetched = await other_store.read()
        assert other_fetched.version == "v1"
        assert other_fetched.sha256 == "hash2"
        other_by_id = {c.concept_id: c for c in other_fetched.concepts}
        assert set(other_by_id) == {"C3"}
        assert other_by_id["C3"].preferred_term == "P3"
        assert other_by_id["C3"].synonyms == frozenset()
        assert other_by_id["C3"].parent_ids == frozenset()
    finally:
        async with get_connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    f"DELETE FROM {ONTOLOGY_CACHE_TABLE} WHERE ontology_id = %s",
                    (ontology_id,),
                )
                await cur.execute(
                    f"DELETE FROM {ONTOLOGY_CACHE_TABLE} WHERE ontology_id = %s",
                    (other_ontology_id,),
                )
