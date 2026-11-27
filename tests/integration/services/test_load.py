import os
import uuid
from abc import abstractmethod
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from pydantic import BaseModel, ConfigDict, Field

from search_api.api.beacon.models import BeaconFilteringOntology, OntologyRestriction
from search_api.api.extract_logs import ExtractLog
from search_api.api.opensearch.models import (
    ONTOLOGY_OTHER_VALUE_FIELD_SUFFIX,
    ExtractedDocument,
    OpenSearchBeaconFilteringTerm,
    OpenSearchField,
    OpenSearchFieldType,
    OpenSearchFieldValue,
    OpenSearchGroup,
)
from search_api.database.document import DOCUMENT_TABLE, get_document
from search_api.database.document_log import (
    DOCUMENT_LOG_TABLE,
    read_document_logs,
)
from search_api.database.models import StoredTerm
from search_api.database.ontology_cache import ONTOLOGY_CACHE_TABLE
from search_api.database.repository import get_cursor
from search_api.database.terms_cache import (
    TERMS_CACHE_TABLE,
    insert_terms,
    read_terms,
)
from search_api.exceptions import SystemException
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

# The id of the test field.
_FIELD_ID = "test_field"
_CONCEPT_ID_PATTERN = r"C\d+"
_CONCEPT_ID_PREFIX_PATTERN = r"C\d+"

# Shaped like a concept id, but in none of the test ontologies.
_UNKNOWN_CONCEPT_ID = "C404"

# The date of the document set the test documents are stored as.
_MODIFIED_AT = datetime(2026, 1, 1, tzinfo=UTC)


class _TestOntology(OntologySource, BaseModel):
    model_config = ConfigDict(frozen=True)

    # A new ontology id for each test.
    id: str = Field(default_factory=lambda: f"TEST-{uuid.uuid4()}")

    @abstractmethod
    def concepts(self) -> list[CachedOntologyConcept]:
        """Return every concept in the ontology."""

    def replacements(self) -> dict[str, str]:
        """Return the concept that replaces each retired concept."""
        return {}

    async def fetch(self) -> CachedOntology:
        return CachedOntology(
            version="2026-01-01", sha256="test", concepts=self.concepts()
        )

    def is_newer(self, version: str, other: str) -> bool:
        return version > other


class SingleConceptOntology(_TestOntology):
    """A test ontology with one concept."""

    concept_id: str = "C1"
    preferred_term: str = "P1"

    def concepts(self) -> list[CachedOntologyConcept]:
        return [
            CachedOntologyConcept(
                concept_id=self.concept_id, preferred_term=self.preferred_term
            )
        ]


class ReplacementOntology(_TestOntology):
    """A test ontology with a retired concept and the concept that replaces it."""

    replaced_concept_id: str = "C2"
    replaced_preferred_term: str = "P2"
    replacement_concept_id: str = "C1"
    replacement_preferred_term: str = "P1"

    def concepts(self) -> list[CachedOntologyConcept]:
        return [
            CachedOntologyConcept(
                concept_id=self.replaced_concept_id,
                preferred_term=self.replaced_preferred_term,
            ),
            CachedOntologyConcept(
                concept_id=self.replacement_concept_id,
                preferred_term=self.replacement_preferred_term,
            ),
        ]

    def replacements(self) -> dict[str, str]:
        return {self.replaced_concept_id: self.replacement_concept_id}


class SharedTermAndParentOntology(_TestOntology):
    """A test ontology with two concepts sharing one preferred term.

    The child concept is under the parent concept. The orphan concept is not.
    """

    parent_concept_id: str = "C3"
    child_concept_id: str = "C4"
    orphan_concept_id: str = "C5"
    shared_preferred_term: str = "P100"

    @property
    def parent_restriction(self) -> OntologyRestriction:
        return OntologyRestriction(
            concept_ids=[self.parent_concept_id], include_descendants=True
        )

    def concepts(self) -> list[CachedOntologyConcept]:
        return [
            CachedOntologyConcept(
                concept_id=self.parent_concept_id, preferred_term="Parent"
            ),
            CachedOntologyConcept(
                concept_id=self.child_concept_id,
                preferred_term=self.shared_preferred_term,
                parent_ids=frozenset({self.parent_concept_id}),
            ),
            CachedOntologyConcept(
                concept_id=self.orphan_concept_id,
                preferred_term=self.shared_preferred_term,
            ),
        ]


class _TestOntologyService(CachedOntologyService):
    def __init__(self, ontology: _TestOntology) -> None:
        super().__init__(
            OntologyCacheStore(ontology.id),
            ontology,
            _CONCEPT_ID_PATTERN,
            _CONCEPT_ID_PREFIX_PATTERN,
        )
        self._replacements = ontology.replacements()

    async def replacement_concept_id(self, concept_id: str) -> str | None:
        return self._replacements.get(concept_id)


@asynccontextmanager
async def _registered_ontology(ontology: _TestOntology) -> AsyncIterator[None]:
    """Register an ontology and delete it and its terms afterwards from the database."""
    register_ontology_service(ontology.id, _TestOntologyService(ontology))
    try:
        yield
    finally:
        async with get_cursor() as cur:
            for table in (ONTOLOGY_CACHE_TABLE, TERMS_CACHE_TABLE):
                await cur.execute(
                    f"DELETE FROM {table} WHERE ontology_id = %s", (ontology.id,)
                )


@pytest_asyncio.fixture
async def single_concept_ontology():
    ontology = SingleConceptOntology()
    async with _registered_ontology(ontology):
        yield ontology


@pytest_asyncio.fixture
async def replacement_ontology():
    ontology = ReplacementOntology()
    async with _registered_ontology(ontology):
        yield ontology


@pytest_asyncio.fixture
async def shared_term_and_parent_ontology():
    ontology = SharedTermAndParentOntology()
    async with _registered_ontology(ontology):
        yield ontology


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


def _ontology_field(
    field_id: str,
    ontology_id: str,
    field_type: OpenSearchFieldType = "ontology",
    restriction: OntologyRestriction | None = None,
    *,
    scopes: Sequence[str] = (),
    nested_group: str | None = None,
) -> OpenSearchBeaconFilteringTerm:
    return OpenSearchBeaconFilteringTerm(
        id=field_id,
        type=field_type,
        scopes=list(scopes),
        nested_group=nested_group,
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
    field = _ontology_field(_FIELD_ID, ontology_id, field_type, restriction)
    document = ExtractedDocument(
        id=document_id,
        values=[OpenSearchFieldValue(field=field, value=(concept_id, meaning))],
    )
    await LoadService(
        create_term_caches({ontology_id}),
        [field],
        replace_concepts=replace_concepts,
    ).store_documents(iter([document]), _MODIFIED_AT, None)


@pytest.mark.asyncio
async def test_load_valid_concept_id(single_concept_ontology, document_id):
    """The concept's preferred term is cached, and nothing is logged against it.

    The document gives the concept id only, so the term comes from the ontology
    rather than the document. It is cached against the field, which is how
    ``/values`` later labels the concept.
    """
    await _load_document_with_ontology_value(
        single_concept_ontology.id, document_id, single_concept_ontology.concept_id
    )

    assert {
        (term.field_id, term.concept_id, term.preferred_term)
        for term in await read_terms(single_concept_ontology.id)
    } == {
        (
            _FIELD_ID,
            single_concept_ontology.concept_id,
            single_concept_ontology.preferred_term,
        )
    }
    async with get_cursor() as cur:
        assert await read_document_logs(cur, document_id) == []


@pytest.mark.asyncio
async def test_load_drops_value_outside_scope(single_concept_ontology, document_id):
    clinical_field = _ontology_field(
        "clinical_field", single_concept_ontology.id, scopes=["clinical"]
    )
    non_clinical_field = _ontology_field(
        "non_clinical_field", single_concept_ontology.id, scopes=["non_clinical"]
    )
    document = ExtractedDocument(
        id=document_id,
        scope="non_clinical",
        values=[
            OpenSearchFieldValue(
                field=field, value=(single_concept_ontology.concept_id, None)
            )
            for field in (clinical_field, non_clinical_field)
        ],
    )

    await LoadService(
        create_term_caches({single_concept_ontology.id}),
        [clinical_field, non_clinical_field],
    ).store_documents(iter([document]), _MODIFIED_AT, None)

    async with get_cursor() as cur:
        assert await get_document(cur, document_id) == {
            "scope": "non_clinical",
            "non_clinical_field": single_concept_ontology.concept_id,
        }
        logs = await read_document_logs(cur, document_id)
    assert [(log.severity, log.message, log.field_id) for log in logs] == [
        (
            "WARNING",
            "Value is ignored: the field is not indexed for scope 'non_clinical'.",
            "clinical_field",
        )
    ]
    assert {
        (term.field_id, term.concept_id, term.preferred_term)
        for term in await read_terms(single_concept_ontology.id)
    } == {
        (
            "non_clinical_field",
            single_concept_ontology.concept_id,
            single_concept_ontology.preferred_term,
        )
    }


@pytest.mark.asyncio
async def test_load_drops_value_without_required_value(
    single_concept_ontology, document_id
):
    """A value is dropped when the value it requires has no resolvable concept."""
    nested_group = "nested_group"
    required_field = _ontology_field(
        "required_field", single_concept_ontology.id, nested_group=nested_group
    )
    requires_field = OpenSearchBeaconFilteringTerm(
        id="requires_field",
        type="keyword",
        nested_group=nested_group,
        scopes=[],
        label="Requires field",
        description="A field that requires required_field.",
        requires=["required_field"],
    )
    document = ExtractedDocument(
        id=document_id,
        groups=[
            OpenSearchGroup(
                group=nested_group,
                values=[
                    OpenSearchFieldValue(
                        field=required_field, value=(_UNKNOWN_CONCEPT_ID, None)
                    ),
                    OpenSearchFieldValue(field=requires_field, value="test"),
                ],
            )
        ],
    )

    await LoadService(
        create_term_caches({single_concept_ontology.id}),
        [required_field, requires_field],
    ).store_documents(iter([document]), _MODIFIED_AT, None)

    async with get_cursor() as cur:
        assert await get_document(cur, document_id) == {}
        logs = await read_document_logs(cur, document_id)
    assert (logs[-1].severity, logs[-1].message, logs[-1].field_id) == (
        "WARNING",
        "Value is ignored: none of the fields it requires has a value.",
        "requires_field",
    )


@pytest.mark.asyncio
async def test_term_caching_failure_does_not_store_the_document(
    single_concept_ontology, document_id, monkeypatch
):
    """A retry skips stored documents, so documents are stored after terms are cached."""
    cache_terms = AsyncMock(side_effect=SystemException("term_caching_failure"))
    monkeypatch.setattr("search_api.services.load.cache_concept_terms", cache_terms)

    with pytest.raises(SystemException, match="term_caching_failure"):
        await _load_document_with_ontology_value(
            single_concept_ontology.id, document_id, single_concept_ontology.concept_id
        )

    cache_terms.assert_awaited_once()
    async with get_cursor() as cur:
        assert await get_document(cur, document_id) is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("provided_concept_id", "quoted_id"),
    [
        # Not well-formed, so not quoted in the message as it may be free text.
        ("invalid", ""),
        # Well-formed, so quoted in the message.
        (_UNKNOWN_CONCEPT_ID, f" '{_UNKNOWN_CONCEPT_ID}'"),
    ],
)
async def test_load_invalid_concept_id(
    single_concept_ontology, document_id, provided_concept_id, quoted_id
):
    """Storing a document with invalid concepts does not cache the preferred term.

    We check that no preferred term are available in the
    preferred terms cache by calling read_terms.
    """
    await _load_document_with_ontology_value(
        single_concept_ontology.id, document_id, provided_concept_id
    )

    async with get_cursor() as cur:
        assert _FIELD_ID not in await get_document(cur, document_id)
        logs = await read_document_logs(cur, document_id)
    assert [(log.severity, log.message) for log in logs] == [
        (
            "WARNING",
            (
                f"The provided concept id{quoted_id} is invalid for "
                f"field '{_FIELD_ID}' of ontology '{single_concept_ontology.id}'."
            ),
        ),
        (
            "ERROR",
            f"Concept id could not be resolved for ontology field '{_FIELD_ID}'.",
        ),
    ]
    assert all(log.field_id == _FIELD_ID for log in logs)
    assert all(log.created_at is not None for log in logs)
    # Empty terms cache.
    assert await read_terms(single_concept_ontology.id) == []


@pytest.mark.asyncio
async def test_replace_concept_true(replacement_ontology, document_id):
    """Test replace_concepts=True."""

    await _load_document_with_ontology_value(
        replacement_ontology.id, document_id, replacement_ontology.replaced_concept_id
    )

    async with get_cursor() as cur:
        document = await get_document(cur, document_id)
        logs = await read_document_logs(cur, document_id)
    assert document[_FIELD_ID] == replacement_ontology.replacement_concept_id
    assert [(log.severity, log.message) for log in logs] == [
        (
            "WARNING",
            (
                f"Provided concept id '{replacement_ontology.replaced_concept_id}' "
                f"was replaced by '{replacement_ontology.replacement_concept_id}' "
                f"for field '{_FIELD_ID}'."
            ),
        )
    ]
    # The replacement is cached.
    assert {
        (term.concept_id, term.preferred_term)
        for term in await read_terms(replacement_ontology.id)
    } == {
        (
            replacement_ontology.replacement_concept_id,
            replacement_ontology.replacement_preferred_term,
        )
    }


@pytest.mark.asyncio
async def test_replace_concept_false(replacement_ontology, document_id):
    """Test replace_concepts=False."""

    await _load_document_with_ontology_value(
        replacement_ontology.id,
        document_id,
        replacement_ontology.replaced_concept_id,
        replace_concepts=False,
    )

    async with get_cursor() as cur:
        document = await get_document(cur, document_id)
        logs = await read_document_logs(cur, document_id)
    assert document[_FIELD_ID] == replacement_ontology.replaced_concept_id
    assert logs == []
    # The replacement is not cached.
    assert {
        (term.concept_id, term.preferred_term)
        for term in await read_terms(replacement_ontology.id)
    } == {
        (
            replacement_ontology.replaced_concept_id,
            replacement_ontology.replaced_preferred_term,
        )
    }


@pytest.mark.asyncio
async def test_resolve_concept_id_from_meaning(single_concept_ontology, document_id):
    """Test invalid concept id with a meaning that resolves to a valid concept id."""
    await _load_document_with_ontology_value(
        single_concept_ontology.id,
        document_id,
        _UNKNOWN_CONCEPT_ID,
        single_concept_ontology.preferred_term.lower(),
    )

    async with get_cursor() as cur:
        document = await get_document(cur, document_id)
        logs = await read_document_logs(cur, document_id)
    assert document[_FIELD_ID] == single_concept_ontology.concept_id
    assert [(log.severity, log.message) for log in logs] == [
        (
            "WARNING",
            (
                f"The provided concept id '{_UNKNOWN_CONCEPT_ID}' is invalid for "
                f"field '{_FIELD_ID}' of ontology '{single_concept_ontology.id}'."
            ),
        ),
        (
            "WARNING",
            (
                f"Concept id '{single_concept_ontology.concept_id}' was resolved "
                "from the provided textual concept value "
                f"'{single_concept_ontology.preferred_term.lower()}' "
                f"for field '{_FIELD_ID}'."
            ),
        ),
    ]
    # Test cached preferred term.
    assert {
        (term.concept_id, term.preferred_term)
        for term in await read_terms(single_concept_ontology.id)
    } == {(single_concept_ontology.concept_id, single_concept_ontology.preferred_term)}


@pytest.mark.asyncio
async def test_resolve_multiple_concepts_id_from_meaning(
    shared_term_and_parent_ontology, document_id
):
    """Test invalid concept id with a meaning that resolves to multiple valid concept id."""
    await _load_document_with_ontology_value(
        shared_term_and_parent_ontology.id,
        document_id,
        _UNKNOWN_CONCEPT_ID,
        shared_term_and_parent_ontology.shared_preferred_term,
    )

    async with get_cursor() as cur:
        document = await get_document(cur, document_id)
        logs = await read_document_logs(cur, document_id)
    assert _FIELD_ID not in document
    assert [(log.severity, log.message) for log in logs] == [
        (
            "WARNING",
            (
                f"The provided concept id '{_UNKNOWN_CONCEPT_ID}' is invalid for "
                f"field '{_FIELD_ID}' "
                f"of ontology '{shared_term_and_parent_ontology.id}'."
            ),
        ),
        (
            "ERROR",
            (
                "Textual concept value "
                f"'{shared_term_and_parent_ontology.shared_preferred_term}' "
                f"resolves to several concept ids for field '{_FIELD_ID}': "
                f"{shared_term_and_parent_ontology.child_concept_id}, "
                f"{shared_term_and_parent_ontology.orphan_concept_id}."
            ),
        ),
        (
            "ERROR",
            f"Concept id could not be resolved for ontology field '{_FIELD_ID}'.",
        ),
    ]


@pytest.mark.asyncio
async def test_resolve_retired_concept_id_from_meaning(
    replacement_ontology, document_id
):
    """Test invalid concept id with a meaning that resolves to retired concept id."""

    await _load_document_with_ontology_value(
        replacement_ontology.id,
        document_id,
        _UNKNOWN_CONCEPT_ID,
        replacement_ontology.replaced_preferred_term,
    )

    async with get_cursor() as cur:
        document = await get_document(cur, document_id)
        logs = await read_document_logs(cur, document_id)
    assert document[_FIELD_ID] == replacement_ontology.replacement_concept_id
    assert [(log.severity, log.message) for log in logs] == [
        (
            "WARNING",
            (
                f"The provided concept id '{_UNKNOWN_CONCEPT_ID}' is invalid for "
                f"field '{_FIELD_ID}' of ontology '{replacement_ontology.id}'."
            ),
        ),
        (
            "WARNING",
            (
                f"Concept id '{replacement_ontology.replaced_concept_id}' was "
                "resolved from the provided textual concept value "
                f"'{replacement_ontology.replaced_preferred_term}' "
                f"for field '{_FIELD_ID}'."
            ),
        ),
        (
            "WARNING",
            (
                f"Provided concept id '{replacement_ontology.replaced_concept_id}' "
                f"was replaced by '{replacement_ontology.replacement_concept_id}' "
                f"for field '{_FIELD_ID}'."
            ),
        ),
    ]


# A field with no ontology.
_KEYWORD_FIELD = OpenSearchBeaconFilteringTerm(
    id=_FIELD_ID,
    type="keyword",
    scopes=[],
    label="Test field",
    description="A keyword field.",
)


def _document_with_log(document_id: str, message: str) -> ExtractedDocument:
    """Return a document with a keyword value and a document log row."""
    return ExtractedDocument(
        id=document_id,
        values=[OpenSearchFieldValue(field=_KEYWORD_FIELD, value="value")],
        logs=[ExtractLog(severity="WARNING", message=message)],
    )


async def _stored_log_messages(document_id: str) -> list[str]:
    async with get_cursor() as cur:
        return [log.message for log in await read_document_logs(cur, document_id)]


@pytest.mark.asyncio
async def test_load_writes_extraction_logs(document_id):
    document = ExtractedDocument(
        id=document_id,
        values=[OpenSearchFieldValue(field=_KEYWORD_FIELD, value="value")],
        logs=[
            ExtractLog(severity="WARNING", message="Something looked odd."),
            ExtractLog(
                severity="ERROR",
                field_id=_FIELD_ID,
                message="Something was ignored.",
            ),
        ],
    )

    await LoadService({}, [_KEYWORD_FIELD]).store_documents(
        iter([document]), _MODIFIED_AT, None
    )

    async with get_cursor() as cur:
        logs = await read_document_logs(cur, document_id)
    assert [(log.severity, log.message, log.field_id) for log in logs] == [
        ("WARNING", "Something looked odd.", None),
        ("ERROR", "Something was ignored.", _FIELD_ID),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("reload", "expected"), [(False, "First load."), (True, "Second load.")]
)
async def test_load_same_version(document_id, reload, expected):
    """The same version is skipped, unless reloaded."""
    await LoadService({}, [_KEYWORD_FIELD]).store_documents(
        iter([_document_with_log(document_id, "First load.")]), _MODIFIED_AT, None
    )
    await LoadService({}, [_KEYWORD_FIELD], reload=reload).store_documents(
        iter([_document_with_log(document_id, "Second load.")]), _MODIFIED_AT, None
    )

    assert await _stored_log_messages(document_id) == [expected]


@pytest.mark.asyncio
async def test_reload_skips_older_version(document_id):
    """A reload never replaces a stored newer version."""
    await LoadService({}, [_KEYWORD_FIELD]).store_documents(
        iter([_document_with_log(document_id, "Newer version.")]), _MODIFIED_AT, None
    )
    await LoadService({}, [_KEYWORD_FIELD], reload=True).store_documents(
        iter([_document_with_log(document_id, "Older version.")]),
        _MODIFIED_AT - timedelta(days=1),
        None,
    )

    assert await _stored_log_messages(document_id) == ["Newer version."]


@pytest.mark.asyncio
async def test_load_transaction(document_id, monkeypatch):
    """A document's log rows and the document are written in one transaction."""
    service = LoadService({}, [_KEYWORD_FIELD])

    await service.store_documents(
        iter([_document_with_log(document_id, "First load.")]), _MODIFIED_AT, None
    )

    # A newer version fails after replacing the logs, before storing the document.
    _store_document = AsyncMock(side_effect=SystemException("store_failure"))
    monkeypatch.setattr(service, "store_document", _store_document)
    with pytest.raises(SystemException, match="store_failure"):
        await service.store_documents(
            iter([_document_with_log(document_id, "Second load.")]),
            _MODIFIED_AT + timedelta(days=1),
            None,
        )

    async with get_cursor() as cur:
        logs = await read_document_logs(cur, document_id)
    assert [(log.severity, log.message, log.field_id) for log in logs] == [
        ("WARNING", "First load.", None)
    ]


@pytest.mark.asyncio
async def test_ambiguous_meaning_resolved_by_restriction(
    shared_term_and_parent_ontology, document_id
):
    """A meaning two cached concepts share resolves to the restricted one.

    An earlier load cached both concepts under the one preferred term, so the
    cache alone cannot say which of them a bare term means. The ontology applies
    the field's restriction, which the cached terms do not, and settles it.
    """
    await insert_terms(
        shared_term_and_parent_ontology.id,
        [
            StoredTerm(
                field_id=_FIELD_ID,
                concept_id=concept_id,
                preferred_term=shared_term_and_parent_ontology.shared_preferred_term,
            )
            for concept_id in (
                shared_term_and_parent_ontology.child_concept_id,
                shared_term_and_parent_ontology.orphan_concept_id,
            )
        ],
    )

    await _load_document_with_ontology_value(
        shared_term_and_parent_ontology.id,
        document_id,
        None,
        shared_term_and_parent_ontology.shared_preferred_term,
        restriction=shared_term_and_parent_ontology.parent_restriction,
    )

    async with get_cursor() as cur:
        document = await get_document(cur, document_id)
        logs = await read_document_logs(cur, document_id)
    assert document[_FIELD_ID] == shared_term_and_parent_ontology.child_concept_id
    assert [(log.severity, log.message) for log in logs] == [
        (
            "WARNING",
            (
                f"Concept id '{shared_term_and_parent_ontology.child_concept_id}' "
                "was resolved from the provided textual concept value "
                f"'{shared_term_and_parent_ontology.shared_preferred_term}' "
                f"for field '{_FIELD_ID}'."
            ),
        )
    ]


@pytest.mark.asyncio
async def test_provided_concept_id_outside_restriction(
    shared_term_and_parent_ontology, document_id
):
    await _load_document_with_ontology_value(
        shared_term_and_parent_ontology.id,
        document_id,
        shared_term_and_parent_ontology.orphan_concept_id,
        restriction=shared_term_and_parent_ontology.parent_restriction,
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
            (
                f"Concept id '{shared_term_and_parent_ontology.orphan_concept_id}' "
                f"is not among the ontology '{shared_term_and_parent_ontology.id}' "
                f"concepts allowed for field '{_FIELD_ID}'."
            ),
        )
    ]
    assert await read_terms(shared_term_and_parent_ontology.id) == []


@pytest.mark.asyncio
async def test_provided_concept_id_within_restriction(
    shared_term_and_parent_ontology, document_id
):
    await _load_document_with_ontology_value(
        shared_term_and_parent_ontology.id,
        document_id,
        shared_term_and_parent_ontology.child_concept_id,
        restriction=shared_term_and_parent_ontology.parent_restriction,
    )

    async with get_cursor() as cur:
        stored = await get_document(cur, document_id)
        logs = await read_document_logs(cur, document_id)
    assert stored[_FIELD_ID] == shared_term_and_parent_ontology.child_concept_id
    assert logs == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "in_ontology",
    [
        # In the ontology, but outside the field restriction.
        pytest.param(True, id="outside_restriction"),
        # Not in the ontology.
        pytest.param(False, id="not_in_ontology"),
    ],
)
async def test_concept_id_already_indexed_is_accepted(
    shared_term_and_parent_ontology, document_id, in_ontology
):
    """A concept id already cached for the field is accepted without consulting the ontology."""
    concept_id = (
        shared_term_and_parent_ontology.orphan_concept_id
        if in_ontology
        else _UNKNOWN_CONCEPT_ID
    )
    await insert_terms(
        shared_term_and_parent_ontology.id,
        [
            StoredTerm(
                field_id=_FIELD_ID,
                concept_id=concept_id,
                preferred_term="Cached by an earlier load",
            )
        ],
    )
    await _load_document_with_ontology_value(
        shared_term_and_parent_ontology.id,
        document_id,
        concept_id,
        restriction=shared_term_and_parent_ontology.parent_restriction,
    )

    async with get_cursor() as cur:
        stored = await get_document(cur, document_id)
        logs = await read_document_logs(cur, document_id)
    assert stored[_FIELD_ID] == concept_id
    assert logs == []


_FREE_TEXT_FIELD_ID = f"{_FIELD_ID}{ONTOLOGY_OTHER_VALUE_FIELD_SUFFIX}"


async def _load_document_with_free_text(
    ontology_id: str, document_id: str, free_text: str
) -> None:
    """Load a document whose one ontologyOrValue field has free text and no code.

    The document is built and stored using the real load, which resolves the
    free text, writes any logs to document_log, stores the document in Postgres,
    and caches the preferred terms of the concepts it resolved.
    """
    field = _ontology_field(_FIELD_ID, ontology_id, "ontologyOrValue")
    free_text_field = OpenSearchField(id=_FREE_TEXT_FIELD_ID, type="keyword")
    document = ExtractedDocument(
        id=document_id,
        values=[OpenSearchFieldValue(field=free_text_field, value=free_text)],
    )
    await LoadService(create_term_caches({ontology_id}), [field]).store_documents(
        iter([document]), _MODIFIED_AT, None
    )


@pytest.mark.asyncio
async def test_resolve_concept_id_from_free_text(single_concept_ontology, document_id):
    """Free text that names one concept is indexed as that concept.

    A search for the text resolves it to the concept, so the document is
    indexed under the concept id, not as free text that no search would reach.
    """
    await _load_document_with_free_text(
        single_concept_ontology.id,
        document_id,
        single_concept_ontology.preferred_term.lower(),
    )

    async with get_cursor() as cur:
        document = await get_document(cur, document_id)
        logs = await read_document_logs(cur, document_id)
    assert document[_FIELD_ID] == single_concept_ontology.concept_id
    assert _FREE_TEXT_FIELD_ID not in document
    assert [(log.severity, log.message) for log in logs] == [
        (
            "WARNING",
            (
                f"Concept id '{single_concept_ontology.concept_id}' was resolved "
                "from the provided textual concept value "
                f"'{single_concept_ontology.preferred_term.lower()}' "
                f"for field '{_FIELD_ID}'."
            ),
        )
    ]
    # read_terms reads the terms cache table. The load cached the concept's
    # preferred term there for this field, which is how /values labels the
    # concept.
    assert {
        (term.field_id, term.concept_id, term.preferred_term)
        for term in await read_terms(single_concept_ontology.id)
    } == {
        (
            _FIELD_ID,
            single_concept_ontology.concept_id,
            single_concept_ontology.preferred_term,
        )
    }


@pytest.mark.asyncio
async def test_resolve_no_concept_id_from_free_text(
    single_concept_ontology, document_id
):
    """Free text that names no concept is indexed as free text, with no log."""
    await _load_document_with_free_text(
        single_concept_ontology.id, document_id, "home-made fixative"
    )

    async with get_cursor() as cur:
        document = await get_document(cur, document_id)
        assert await read_document_logs(cur, document_id) == []
    assert document[_FREE_TEXT_FIELD_ID] == "home-made fixative"
    assert _FIELD_ID not in document


@pytest.mark.asyncio
async def test_resolve_multiple_concepts_id_from_free_text(
    shared_term_and_parent_ontology, document_id
):
    """Free text that names several concepts is indexed as free text.

    No single concept can be chosen, which is logged as for a meaning.
    """
    await _load_document_with_free_text(
        shared_term_and_parent_ontology.id,
        document_id,
        shared_term_and_parent_ontology.shared_preferred_term,
    )

    async with get_cursor() as cur:
        document = await get_document(cur, document_id)
        logs = await read_document_logs(cur, document_id)
    assert (
        document[_FREE_TEXT_FIELD_ID]
        == shared_term_and_parent_ontology.shared_preferred_term
    )
    assert _FIELD_ID not in document
    assert [(log.severity, log.message) for log in logs] == [
        (
            "ERROR",
            (
                "Textual concept value "
                f"'{shared_term_and_parent_ontology.shared_preferred_term}' "
                f"resolves to several concept ids for field '{_FIELD_ID}': "
                f"{shared_term_and_parent_ontology.child_concept_id}, "
                f"{shared_term_and_parent_ontology.orphan_concept_id}."
            ),
        )
    ]
