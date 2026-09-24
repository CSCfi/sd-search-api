import os
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
import pytest_asyncio

from search_api.api.beacon.models import BeaconFilteringOntology, OntologyRestriction
from search_api.api.extract_logs import ExtractLog
from search_api.api.opensearch.models import (
    ExtractedDocument,
    OpenSearchFieldType,
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
from search_api.database.models import StoredTerm
from search_api.database.terms_cache import (
    TERMS_CACHE_TABLE,
    insert_terms,
    read_terms,
)
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

# The one ontology field these tests load a document for.
_FIELD_ID = "test_field"
_CONCEPT_ID_PATTERN = r"C\d+"
_CONCEPT_ID_PREFIX_PATTERN = r"C\d+"

# The `single_concept_ontology_id` contains _CONCEPT_ID.
_CONCEPT_ID = "C1"
_PREFERRED_TERM = "P1"

# The `replacement_ontology_id` replaces _REPLACED_CONCEPT_ID
# with _CONCEPT_ID.
_REPLACED_CONCEPT_ID = "C2"
_REPLACED_PREFERRED_TERM = "P2"
_REPLACEMENT_CONCEPT_ID = _CONCEPT_ID

# The `shared_term_and_parent_ontology_id` has three concepts. The
# _CHILD_CONCEPT_ID is under the _PARENT_CONCEPT_ID, while the
# _ORPHAN_CONCEPT_ID is not.
_PARENT_CONCEPT_ID = "C3"
_CHILD_CONCEPT_ID = "C4"
_ORPHAN_CONCEPT_ID = "C5"
_SHARED_PREFERRED_TERM = (
    "P100"  # shared by the _CHILD_CONCEPT_ID and _ORPHAN_CONCEPT_ID
)

_PARENT_RESTRICTION = OntologyRestriction(
    concept_ids=[_PARENT_CONCEPT_ID], include_descendants=True
)

# Shaped like a concept id, but in none of the ontologies above.
_UNKNOWN_CONCEPT_ID = "C404"


class OntologySourceWithOneConcept(OntologySource):
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


class OntologySourceWithReplacedConcept(OntologySource):
    async def fetch(self) -> CachedOntology:
        return CachedOntology(
            version="2026-01-01",
            sha256="test",
            concepts=[
                CachedOntologyConcept(
                    concept_id=_CONCEPT_ID, preferred_term=_PREFERRED_TERM
                ),
                CachedOntologyConcept(
                    concept_id=_REPLACED_CONCEPT_ID,
                    preferred_term=_REPLACED_PREFERRED_TERM,
                ),
            ],
        )

    def is_newer(self, version: str, other: str) -> bool:
        return version > other


class OntologySourceWithSharedTermAndParent(OntologySource):
    async def fetch(self) -> CachedOntology:
        return CachedOntology(
            version="2026-01-01",
            sha256="test",
            concepts=[
                CachedOntologyConcept(
                    concept_id=_PARENT_CONCEPT_ID, preferred_term="Parent"
                ),
                CachedOntologyConcept(
                    concept_id=_CHILD_CONCEPT_ID,
                    preferred_term=_SHARED_PREFERRED_TERM,
                    parent_ids=frozenset({_PARENT_CONCEPT_ID}),
                ),
                CachedOntologyConcept(
                    concept_id=_ORPHAN_CONCEPT_ID,
                    preferred_term=_SHARED_PREFERRED_TERM,
                ),
            ],
        )

    def is_newer(self, version: str, other: str) -> bool:
        return version > other


class OntologyServiceReplacingRetiredConcept(CachedOntologyService):
    async def replacement_concept_id(self, concept_id: str) -> str | None:
        return _REPLACEMENT_CONCEPT_ID if concept_id == _REPLACED_CONCEPT_ID else None


@asynccontextmanager
async def _registered_ontology(
    source: OntologySource,
    service_type: type[CachedOntologyService] = CachedOntologyService,
) -> AsyncIterator[str]:
    """Register an ontology and delete it and its terms afterwards from the database."""
    ontology_id = f"TEST-{uuid.uuid4()}"
    register_ontology_service(
        ontology_id,
        service_type(
            OntologyCacheStore(ontology_id),
            source,
            _CONCEPT_ID_PATTERN,
            _CONCEPT_ID_PREFIX_PATTERN,
        ),
    )
    try:
        yield ontology_id
    finally:
        async with get_cursor() as cur:
            for table in (ONTOLOGY_CACHE_TABLE, TERMS_CACHE_TABLE):
                await cur.execute(
                    f"DELETE FROM {table} WHERE ontology_id = %s", (ontology_id,)
                )


@pytest_asyncio.fixture
async def single_concept_ontology_id():
    async with _registered_ontology(OntologySourceWithOneConcept()) as ontology_id:
        yield ontology_id


@pytest_asyncio.fixture
async def replacement_ontology_id():
    async with _registered_ontology(
        OntologySourceWithReplacedConcept(), OntologyServiceReplacingRetiredConcept
    ) as ontology_id:
        yield ontology_id


@pytest_asyncio.fixture
async def shared_term_and_parent_ontology_id():
    async with _registered_ontology(
        OntologySourceWithSharedTermAndParent()
    ) as ontology_id:
        yield ontology_id


async def _delete_documents(document_ids: list[str]) -> None:
    async with get_cursor() as cur:
        for document_id in document_ids:
            await cur.execute(
                f"DELETE FROM {DOCUMENT_TABLE} WHERE id = %s", (document_id,)
            )
            await cur.execute(
                f"DELETE FROM {DOCUMENT_LOG_TABLE} WHERE document_id = %s",
                (document_id,),
            )


@pytest_asyncio.fixture
async def document_id():
    """Returns a document id and deletes it afterwards from the database."""
    document_id = f"image-{uuid.uuid4()}"
    yield document_id
    await _delete_documents([document_id])


@pytest_asyncio.fixture
async def document_ids():
    """Creates document ids and deletes them afterwards from the database."""
    created: list[str] = []

    def next_document_id() -> str:
        created.append(f"image-{uuid.uuid4()}")
        return created[-1]

    yield next_document_id
    await _delete_documents(created)


def _ontology_field(
    ontology_id: str,
    field_type: OpenSearchFieldType = "ontology",
    restriction: OntologyRestriction | None = None,
) -> OpenSearchBeaconFilteringTerm:
    return OpenSearchBeaconFilteringTerm(
        id=_FIELD_ID,
        type=field_type,
        scopes=[],
        label="Test field",
        description="A field resolving against the test ontology.",
        ontology=BeaconFilteringOntology(id=ontology_id),
        ontologyRestriction=restriction,
    )


async def _load_document_with_ontology_value(
    ontology_id: str,
    document_id: str,
    concept_id: str | None,
    meaning: str | None = None,
    *,
    field_type: OpenSearchFieldType = "ontology",
    restriction: OntologyRestriction | None = None,
    replace_concepts: bool = True,
) -> None:
    """Load a document whose one ontology field has the concept id and meaning."""
    field = _ontology_field(ontology_id, field_type, restriction)
    document = ExtractedDocument(
        id=document_id,
        values=[OpenSearchFieldValue(field=field, value=(concept_id, meaning))],
    )
    await LoadService(
        create_term_caches({ontology_id}),
        [field],
        replace_concepts=replace_concepts,
    ).store_documents(iter([document]))


@pytest.mark.asyncio
async def test_load_valid_concept_id(single_concept_ontology_id, document_id):
    """The concept's preferred term is cached, and nothing is logged against it.

    The document gives the concept id only, so the term comes from the ontology
    rather than the document. It is cached against the field, which is how
    ``/values`` later labels the concept.
    """
    await _load_document_with_ontology_value(
        single_concept_ontology_id, document_id, _CONCEPT_ID
    )

    assert {
        (term.field_id, term.concept_id, term.preferred_term)
        for term in await read_terms(single_concept_ontology_id)
    } == {(_FIELD_ID, _CONCEPT_ID, _PREFERRED_TERM)}
    async with get_cursor() as cur:
        assert await read_document_logs(cur, document_id) == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "provided_concept_id",
    [
        "invalid",  # Not well-formed
        _UNKNOWN_CONCEPT_ID,
    ],
)
async def test_load_invalid_concept_id(
    single_concept_ontology_id, document_id, provided_concept_id
):
    """Storing a document with invalid concepts does not cache the preferred term.

    We check that no preferred term are available in the
    preferred terms cache by calling read_terms.
    """
    await _load_document_with_ontology_value(
        single_concept_ontology_id, document_id, provided_concept_id
    )

    async with get_cursor() as cur:
        assert _FIELD_ID not in await get_document(cur, document_id)
        logs = await read_document_logs(cur, document_id)
    assert [(log.severity, log.message) for log in logs] == [
        (
            "WARNING",
            f"The provided concept id '{provided_concept_id}' is invalid for "
            f"field '{_FIELD_ID}' of ontology '{single_concept_ontology_id}'.",
        ),
        (
            "ERROR",
            f"Concept id could not be resolved for ontology field '{_FIELD_ID}'.",
        ),
    ]
    assert all(log.field_id == _FIELD_ID for log in logs)
    assert all(log.created_at is not None for log in logs)
    # Empty terms cache.
    assert await read_terms(single_concept_ontology_id) == []


@pytest.mark.asyncio
async def test_replace_concept_true(replacement_ontology_id, document_id):
    """Test replace_concepts=True."""

    await _load_document_with_ontology_value(
        replacement_ontology_id, document_id, _REPLACED_CONCEPT_ID
    )

    async with get_cursor() as cur:
        document = await get_document(cur, document_id)
        logs = await read_document_logs(cur, document_id)
    assert document[_FIELD_ID] == _REPLACEMENT_CONCEPT_ID
    assert [(log.severity, log.message) for log in logs] == [
        (
            "WARNING",
            f"Provided concept id '{_REPLACED_CONCEPT_ID}' was replaced by "
            f"'{_REPLACEMENT_CONCEPT_ID}' for field '{_FIELD_ID}'.",
        )
    ]
    # The replacement is cached.
    assert {
        (term.concept_id, term.preferred_term)
        for term in await read_terms(replacement_ontology_id)
    } == {(_REPLACEMENT_CONCEPT_ID, _PREFERRED_TERM)}


@pytest.mark.asyncio
async def test_replace_concept_false(replacement_ontology_id, document_id):
    """Test replace_concepts=False."""

    await _load_document_with_ontology_value(
        replacement_ontology_id,
        document_id,
        _REPLACED_CONCEPT_ID,
        replace_concepts=False,
    )

    async with get_cursor() as cur:
        document = await get_document(cur, document_id)
        logs = await read_document_logs(cur, document_id)
    assert document[_FIELD_ID] == _REPLACED_CONCEPT_ID
    assert logs == []
    # The replacement is not cached.
    assert {
        (term.concept_id, term.preferred_term)
        for term in await read_terms(replacement_ontology_id)
    } == {(_REPLACED_CONCEPT_ID, _REPLACED_PREFERRED_TERM)}


@pytest.mark.asyncio
async def test_resolve_concept_id_from_meaning(single_concept_ontology_id, document_id):
    """Test invalid concept id with a meaning that resolves to a valid concept id."""
    await _load_document_with_ontology_value(
        single_concept_ontology_id,
        document_id,
        _UNKNOWN_CONCEPT_ID,
        _PREFERRED_TERM.lower(),
    )

    async with get_cursor() as cur:
        document = await get_document(cur, document_id)
        logs = await read_document_logs(cur, document_id)
    assert document[_FIELD_ID] == _CONCEPT_ID
    assert [(log.severity, log.message) for log in logs] == [
        (
            "WARNING",
            f"The provided concept id 'C404' is invalid for field '{_FIELD_ID}' "
            f"of ontology '{single_concept_ontology_id}'.",
        ),
        (
            "WARNING",
            f"Concept id '{_CONCEPT_ID}' was resolved from the provided textual "
            f"concept value '{_PREFERRED_TERM.lower()}' for field '{_FIELD_ID}'.",
        ),
    ]
    # Test cached preferred term.
    assert {
        (term.concept_id, term.preferred_term)
        for term in await read_terms(single_concept_ontology_id)
    } == {(_CONCEPT_ID, _PREFERRED_TERM)}


@pytest.mark.asyncio
async def test_resolve_multiple_concepts_id_from_meaning(
    shared_term_and_parent_ontology_id, document_id
):
    """Test invalid concept id with a meaning that resolves to multiple valid concept id."""
    await _load_document_with_ontology_value(
        shared_term_and_parent_ontology_id,
        document_id,
        _UNKNOWN_CONCEPT_ID,
        _SHARED_PREFERRED_TERM,
    )

    async with get_cursor() as cur:
        document = await get_document(cur, document_id)
        logs = await read_document_logs(cur, document_id)
    assert _FIELD_ID not in document
    assert [(log.severity, log.message) for log in logs] == [
        (
            "WARNING",
            f"The provided concept id 'C404' is invalid for field '{_FIELD_ID}' "
            f"of ontology '{shared_term_and_parent_ontology_id}'.",
        ),
        (
            "ERROR",
            f"Textual concept value '{_SHARED_PREFERRED_TERM}' resolves to several "
            f"concept ids for field '{_FIELD_ID}': "
            f"{_CHILD_CONCEPT_ID}, {_ORPHAN_CONCEPT_ID}.",
        ),
        (
            "ERROR",
            f"Concept id could not be resolved for ontology field '{_FIELD_ID}'.",
        ),
    ]


@pytest.mark.asyncio
async def test_resolve_retired_concept_id_from_meaning(
    replacement_ontology_id, document_id
):
    """Test invalid concept id with a meaning that resolves to retired concept id."""

    await _load_document_with_ontology_value(
        replacement_ontology_id,
        document_id,
        _UNKNOWN_CONCEPT_ID,
        _REPLACED_PREFERRED_TERM,
    )

    async with get_cursor() as cur:
        document = await get_document(cur, document_id)
        logs = await read_document_logs(cur, document_id)
    assert document[_FIELD_ID] == _REPLACEMENT_CONCEPT_ID
    assert [(log.severity, log.message) for log in logs] == [
        (
            "WARNING",
            f"The provided concept id 'C404' is invalid for field '{_FIELD_ID}' "
            f"of ontology '{replacement_ontology_id}'.",
        ),
        (
            "WARNING",
            f"Concept id '{_REPLACED_CONCEPT_ID}' was resolved from the provided "
            f"textual concept value '{_REPLACED_PREFERRED_TERM}' "
            f"for field '{_FIELD_ID}'.",
        ),
        (
            "WARNING",
            f"Provided concept id '{_REPLACED_CONCEPT_ID}' was replaced by "
            f"'{_REPLACEMENT_CONCEPT_ID}' for field '{_FIELD_ID}'.",
        ),
    ]


@pytest.mark.asyncio
async def test_load_writes_extraction_logs(single_concept_ontology_id, document_id):
    field = _ontology_field(single_concept_ontology_id)
    document = ExtractedDocument(
        id=document_id,
        values=[OpenSearchFieldValue(field=field, value=(_CONCEPT_ID, None))],
        logs=[
            ExtractLog(severity="WARNING", message="Something looked odd."),
            ExtractLog(
                severity="ERROR",
                field_id=_FIELD_ID,
                message="Something was ignored.",
            ),
        ],
    )

    await LoadService(
        create_term_caches({single_concept_ontology_id}), [field]
    ).store_documents(iter([document]))

    async with get_cursor() as cur:
        logs = await read_document_logs(cur, document_id)
    assert [(log.severity, log.message, log.field_id) for log in logs] == [
        ("WARNING", "Something looked odd.", None),
        ("ERROR", "Something was ignored.", _FIELD_ID),
    ]


@pytest.mark.asyncio
async def test_ambiguous_meaning_resolved_by_restriction(
    shared_term_and_parent_ontology_id, document_id
):
    """A meaning two cached concepts share resolves to the restricted one.

    An earlier load cached both concepts under the one preferred term, so the
    cache alone cannot say which of them a bare term means. The ontology applies
    the field's restriction, which the cached terms do not, and settles it.
    """
    await insert_terms(
        shared_term_and_parent_ontology_id,
        [
            StoredTerm(
                field_id=_FIELD_ID,
                concept_id=concept_id,
                preferred_term=_SHARED_PREFERRED_TERM,
            )
            for concept_id in (_CHILD_CONCEPT_ID, _ORPHAN_CONCEPT_ID)
        ],
    )

    await _load_document_with_ontology_value(
        shared_term_and_parent_ontology_id,
        document_id,
        None,
        _SHARED_PREFERRED_TERM,
        restriction=_PARENT_RESTRICTION,
    )

    async with get_cursor() as cur:
        document = await get_document(cur, document_id)
        logs = await read_document_logs(cur, document_id)
    assert document[_FIELD_ID] == _CHILD_CONCEPT_ID
    assert [(log.severity, log.message) for log in logs] == [
        (
            "WARNING",
            f"Concept id '{_CHILD_CONCEPT_ID}' was resolved from the provided "
            f"textual concept value '{_SHARED_PREFERRED_TERM}' for field '{_FIELD_ID}'.",
        )
    ]


@pytest.mark.asyncio
async def test_provided_concept_id_outside_restriction(
    shared_term_and_parent_ontology_id, document_id
):
    await _load_document_with_ontology_value(
        shared_term_and_parent_ontology_id,
        document_id,
        _ORPHAN_CONCEPT_ID,
        restriction=_PARENT_RESTRICTION,
    )

    async with get_cursor() as cur:
        stored = await get_document(cur, document_id)
        logs = await read_document_logs(cur, document_id)
    # The concept id is well formed and in the ontology, so only the restriction
    # rejects it. Nothing is indexed and nothing is cached for it.
    assert _FIELD_ID not in stored
    assert [(log.severity, log.message) for log in logs] == [
        (
            "ERROR",
            f"Concept id '{_ORPHAN_CONCEPT_ID}' is not among the ontology "
            f"'{shared_term_and_parent_ontology_id}' concepts allowed for field '{_FIELD_ID}'.",
        )
    ]
    assert await read_terms(shared_term_and_parent_ontology_id) == []


@pytest.mark.asyncio
async def test_provided_concept_id_within_restriction(
    shared_term_and_parent_ontology_id, document_id
):
    await _load_document_with_ontology_value(
        shared_term_and_parent_ontology_id,
        document_id,
        _CHILD_CONCEPT_ID,
        restriction=_PARENT_RESTRICTION,
    )

    async with get_cursor() as cur:
        stored = await get_document(cur, document_id)
        logs = await read_document_logs(cur, document_id)
    assert stored[_FIELD_ID] == _CHILD_CONCEPT_ID
    assert logs == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "concept_id",
    [
        # In the ontology, but outside the field restriction,
        _ORPHAN_CONCEPT_ID,
        # Not in the ontology.
        _UNKNOWN_CONCEPT_ID,
    ],
)
async def test_concept_id_already_indexed_is_accepted(
    shared_term_and_parent_ontology_id, document_id, concept_id
):
    """A concept id already cached for the field is accepted without consulting the ontology."""
    await insert_terms(
        shared_term_and_parent_ontology_id,
        [
            StoredTerm(
                field_id=_FIELD_ID,
                concept_id=concept_id,
                preferred_term="Cached by an earlier load",
            )
        ],
    )
    await _load_document_with_ontology_value(
        shared_term_and_parent_ontology_id,
        document_id,
        concept_id,
        restriction=_PARENT_RESTRICTION,
    )

    async with get_cursor() as cur:
        stored = await get_document(cur, document_id)
        logs = await read_document_logs(cur, document_id)
    assert stored[_FIELD_ID] == concept_id
    assert logs == []
