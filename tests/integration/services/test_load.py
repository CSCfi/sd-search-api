import os
import re
import uuid

import pytest
import pytest_asyncio

from search_api.api.beacon.models import BeaconFilteringOntology
from search_api.api.opensearch.models import (
    ExtractedDocument,
    OpenSearchBeaconFilteringTerm,
    OpenSearchFieldValue,
)
from search_api.database.document import DOCUMENT_TABLE, get_document
from search_api.database.document_log import (
    DOCUMENT_LOG_TABLE,
    read_document_logs,
)
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

# A concept the ontology has retired, and the active one replacing it. Retiring a
# concept does not remove it — SNOMED CT resolves a retired id to its preferred term
# as readily as an active one — so the source below serves both.
_RETIRED_CONCEPT_ID = "C9"
_RETIRED_PREFERRED_TERM = "P9"
_REPLACEMENT_CONCEPT_ID = _CONCEPT_ID


class _Source(OntologySource):
    """Serves one active concept and one the ontology has retired."""

    async def fetch(self) -> CachedOntology:
        return CachedOntology(
            version="2026-01-01",
            sha256="test",
            concepts=[
                CachedOntologyConcept(
                    concept_id=_CONCEPT_ID, preferred_term=_PREFERRED_TERM
                ),
                CachedOntologyConcept(
                    concept_id=_RETIRED_CONCEPT_ID,
                    preferred_term=_RETIRED_PREFERRED_TERM,
                ),
            ],
        )

    def is_newer(self, version: str, other: str) -> bool:
        return version > other


class _ReplacingService(CachedOntologyService):
    """Takes ``C9`` for a concept the ontology has replaced by ``C1``."""

    def is_concept_id(self, value: str) -> bool:
        return value in (_RETIRED_CONCEPT_ID, _REPLACEMENT_CONCEPT_ID)

    async def replacement_concept_id(self, concept_id: str) -> str | None:
        return _REPLACEMENT_CONCEPT_ID if concept_id == _RETIRED_CONCEPT_ID else None


class _IsConceptIdService(CachedOntologyService):
    """Accepts any ``C<digits>`` as a concept id."""

    def is_concept_id(self, value: str) -> bool:
        return bool(re.fullmatch(r"C\d+", value))


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
    """A document of its own, deleted afterwards with anything logged for it."""
    document_id = f"image-{uuid.uuid4()}"
    yield document_id
    async with get_cursor() as cur:
        await cur.execute(f"DELETE FROM {DOCUMENT_TABLE} WHERE id = %s", (document_id,))
        await cur.execute(
            f"DELETE FROM {DOCUMENT_LOG_TABLE} WHERE document_id = %s", (document_id,)
        )


def _ontology_field(ontology_id: str) -> OpenSearchBeaconFilteringTerm:
    return OpenSearchBeaconFilteringTerm(
        id="test_field",
        type="ontology",
        scopes=[],
        label="Test field",
        description="A field resolving against the test ontology.",
        ontology=BeaconFilteringOntology(id=ontology_id),
    )


@pytest.mark.asyncio
async def test_load_caches_preferred_term(ontology_id, document_id):
    field = _ontology_field(ontology_id)
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


async def _load_document_with_ontology_value(
    ontology_id: str, document_id: str, value: str, replace_concepts: bool = True
) -> None:
    """Load a document whose one ontology field carries this value."""
    field = _ontology_field(ontology_id)
    document = ExtractedDocument(
        id=document_id,
        values=[OpenSearchFieldValue(field=field, value=value)],
    )
    await LoadService(
        create_term_caches({ontology_id}),
        [field],
        replace_concepts=replace_concepts,
    ).store_documents(iter([document]))


@pytest.mark.asyncio
async def test_load_logs_no_concept_id(ontology_id, document_id):
    unknown_value = "C404"

    await _load_document_with_ontology_value(ontology_id, document_id, unknown_value)

    async with get_cursor() as cur:
        logs = await read_document_logs(cur, document_id)
    assert len(logs) == 1
    entry = logs[0]
    assert entry.severity == "ERROR"
    assert entry.field_id == "test_field"
    assert entry.message == (
        f"Value '{unknown_value}' is no concept id of ontology '{ontology_id}'."
    )
    assert entry.created_at is not None


@pytest.mark.asyncio
async def test_load_logs_not_found_concept_id(ontology_id, document_id):
    register_ontology_service(
        ontology_id,
        _IsConceptIdService(OntologyCacheStore(ontology_id), _Source()),
    )
    unknown_concept_id = "C404"

    await _load_document_with_ontology_value(
        ontology_id, document_id, unknown_concept_id
    )

    async with get_cursor() as cur:
        logs = await read_document_logs(cur, document_id)
    assert len(logs) == 1
    assert logs[0].message == (
        f"Value '{unknown_concept_id}' was not found in ontology '{ontology_id}'."
    )


@pytest.mark.asyncio
async def test_load_logs_nothing_for_valid_concept_id(ontology_id, document_id):
    field = _ontology_field(ontology_id)
    document = ExtractedDocument(
        id=document_id,
        values=[OpenSearchFieldValue(field=field, value=_CONCEPT_ID)],
    )
    service = LoadService(create_term_caches({ontology_id}), [field])

    await service.store_documents(iter([document]))

    async with get_cursor() as cur:
        assert await read_document_logs(cur, document_id) == []


@pytest.mark.asyncio
async def test_load_substitutes_a_replaced_concept(ontology_id, document_id):
    """A retired concept is stored as its replacement, and the swap is logged.

    The document is stored with the replacement, so its facet entry and any subtree
    search reach it, while the log keeps what the source said.
    """
    register_ontology_service(
        ontology_id,
        _ReplacingService(OntologyCacheStore(ontology_id), _Source()),
    )

    await _load_document_with_ontology_value(
        ontology_id, document_id, _RETIRED_CONCEPT_ID
    )

    async with get_cursor() as cur:
        payload = await get_document(cur, document_id)
        logs = await read_document_logs(cur, document_id)
    assert payload["test_field"] == _REPLACEMENT_CONCEPT_ID
    assert [(log.severity, log.message) for log in logs] == [
        (
            "WARNING",
            f"Value '{_RETIRED_CONCEPT_ID}' is replaced by "
            f"'{_REPLACEMENT_CONCEPT_ID}' in ontology '{ontology_id}'.",
        )
    ]
    # The replacement is what a preferred term is cached for.
    assert {
        (term.concept_id, term.preferred_term) for term in await read_terms(ontology_id)
    } == {(_REPLACEMENT_CONCEPT_ID, _PREFERRED_TERM)}


@pytest.mark.asyncio
async def test_load_keeps_a_replaced_concept_when_substitution_is_off(
    ontology_id, document_id
):
    """``replace_concepts=False`` indexes what the source said, unchanged.

    The ontology still names a replacement, so only the flag stands between this
    and the substitution the previous test asserts. The retired concept keeps its
    own preferred term, so the value is still named in the field's values and
    nothing is logged against the document.
    """
    register_ontology_service(
        ontology_id,
        _ReplacingService(OntologyCacheStore(ontology_id), _Source()),
    )

    await _load_document_with_ontology_value(
        ontology_id, document_id, _RETIRED_CONCEPT_ID, replace_concepts=False
    )

    async with get_cursor() as cur:
        payload = await get_document(cur, document_id)
        logs = await read_document_logs(cur, document_id)
    assert payload["test_field"] == _RETIRED_CONCEPT_ID
    assert logs == []
    # The retired concept is what a preferred term is cached for, so the value is
    # named in the field's values rather than missing from them.
    assert {
        (term.concept_id, term.preferred_term) for term in await read_terms(ontology_id)
    } == {(_RETIRED_CONCEPT_ID, _RETIRED_PREFERRED_TERM)}
