import os
import uuid

import pytest
import pytest_asyncio

from search_api.api.beacon.models import BeaconFilteringOntology
from search_api.api.opensearch.models import (
    ExtractedDocument,
    OpenSearchBeaconFilteringTerm,
    OpenSearchFieldValue,
)
from search_api.database.document import DOCUMENT_TABLE
from search_api.database.ontology_cache import ONTOLOGY_CACHE_TABLE
from search_api.database.repository import get_cursor
from search_api.database.terms_cache import TERMS_CACHE_TABLE, read_terms
from search_api.services.load import LoadService
from search_api.services.ontology.cache.models import (
    CachedOntology,
    CachedOntologyConcept,
)
from search_api.services.ontology.cache.service import CachedOntologyService
from search_api.services.ontology.cache.source import OntologySource
from search_api.services.ontology.cache.store import OntologyCacheStore
from search_api.services.ontology.service import register_ontology_service
from search_api.services.ontology.term_cache import create_term_caches

os.environ.setdefault("POSTGRES_DB", os.environ["BP_POSTGRES_DB"])
os.environ.setdefault("POSTGRES_PORT", os.environ["BP_POSTGRES_PORT"])

_CONCEPT_ID = "C1"
_PREFERRED_TERM = "P1"


class _Source(OntologySource):
    """Serves one concept"""

    async def fetch(self) -> CachedOntology:
        return CachedOntology(
            version="2026-01-01",
            sha256="test",
            concepts=[
                CachedOntologyConcept(
                    concept_id=_CONCEPT_ID, preferred_term=_PREFERRED_TERM
                )
            ],
        )

    def is_newer(self, version: str, other: str) -> bool:
        return version > other


@pytest_asyncio.fixture
async def ontology_id():
    """An ontology of its own, cached whole like SEND, deleted afterwards."""
    ontology_id = f"TEST-{uuid.uuid4()}"
    register_ontology_service(
        ontology_id,
        CachedOntologyService(OntologyCacheStore(ontology_id), _Source()),
    )
    yield ontology_id
    async with get_cursor() as cur:
        for table in (ONTOLOGY_CACHE_TABLE, TERMS_CACHE_TABLE):
            await cur.execute(
                f"DELETE FROM {table} WHERE ontology_id = %s", (ontology_id,)
            )


@pytest_asyncio.fixture
async def document_id():
    """A document of its own, deleted afterwards."""
    document_id = f"image-{uuid.uuid4()}"
    yield document_id
    async with get_cursor() as cur:
        await cur.execute(f"DELETE FROM {DOCUMENT_TABLE} WHERE id = %s", (document_id,))


@pytest.mark.asyncio
async def test_load_caches_preferred_term(ontology_id, document_id):
    field = OpenSearchBeaconFilteringTerm(
        id="test_field",
        type="ontology",
        scopes=[],
        label="Test field",
        description="A field resolving against the test ontology.",
        ontology=BeaconFilteringOntology(id=ontology_id),
    )
    document = ExtractedDocument(
        id=document_id,
        values=[OpenSearchFieldValue(field=field, value=_CONCEPT_ID)],
    )
    service = LoadService(create_term_caches({ontology_id}), [field])

    await service.store_documents(iter([document]))

    assert {
        (term.field_id, term.concept_id, term.preferred_term)
        for term in await read_terms(ontology_id)
    } == {(field.id, _CONCEPT_ID, _PREFERRED_TERM)}
