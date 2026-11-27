"""Boundary validation of extracted documents against the deployment's config."""

from unittest.mock import AsyncMock

import pytest

from search_api.api.beacon.models import BeaconFilteringOntology
from search_api.api.bigpicture.domain import BP_DOMAIN
from search_api.api.opensearch.models import (
    MAX_DOCUMENT_ID_BYTES,
    MAX_KEYWORD_BYTES,
    ExtractedDocument,
    OpenSearchBeaconFilteringTerm,
    OpenSearchField,
    OpenSearchFieldValue,
    OpenSearchGroup,
)
from search_api.database.models import StoredDocumentLog
from search_api.exceptions import SystemException, UserException
from search_api.services.load import (
    LoadService,
    _indexed_field_ids,
    drop_out_of_scope_values,
    drop_too_long_keywords,
    drop_values_without_required,
)
from search_api.services.ontology.term_cache import create_term_caches


def _load_service(**overrides) -> LoadService:
    kwargs = {
        "term_caches": create_term_caches(BP_DOMAIN.ontology_ids),
        "filtering_terms": BP_DOMAIN.filtering_terms,
        "filtering_scopes": BP_DOMAIN.filtering_scopes,
    }
    kwargs.update(overrides)
    return LoadService(**kwargs)


def _document(scope="clinical") -> ExtractedDocument:
    return ExtractedDocument(
        id="img-1",
        scope=scope,
        groups=[
            OpenSearchGroup(
                group="diagnosis",
                values=[
                    OpenSearchFieldValue(
                        field=OpenSearchField(
                            id="diagnosis", type="ontology", nested_group="diagnosis"
                        ),
                        value=("73211009", None),
                    )
                ],
            )
        ],
    )


def test_validate_document_accepts_declared_scope():
    _load_service().validate_document(_document())


def test_validate_document_rejects_undeclared_scope():
    with pytest.raises(UserException, match="Unsupported scope: 'preclinical'"):
        _load_service().validate_document(_document(scope="preclinical"))


def test_validate_document_rejects_missing_scope():
    """A document must carry a scope so a scoped query cannot silently miss it."""
    with pytest.raises(UserException, match="Missing scope"):
        _load_service().validate_document(_document(scope=None))


def test_validate_document_allows_missing_scope_without_declared_scopes():
    _load_service(filtering_scopes=()).validate_document(_document(scope=None))


def test_validate_document_names_the_offending_document():
    with pytest.raises(UserException, match="Document 'img-1'"):
        _load_service().validate_document(_document(scope="preclinical"))


def test_validate_document_rejects_too_long_id():
    """OpenSearch rejects a document id longer than 512 bytes."""
    # The longest id accepted, one byte per character.
    _load_service().validate_document(
        _document().model_copy(update={"id": "i" * MAX_DOCUMENT_ID_BYTES})
    )

    with pytest.raises(SystemException) as exc:
        _load_service().validate_document(
            _document().model_copy(update={"id": "i" * (MAX_DOCUMENT_ID_BYTES + 1)})
        )
    assert (
        str(exc.value) == f"Document id is longer than {MAX_DOCUMENT_ID_BYTES} bytes."
    )


def _keyword(field_id: str, value: str, nested_group: str | None = None):
    return OpenSearchFieldValue(
        field=OpenSearchField(id=field_id, type="keyword", nested_group=nested_group),
        value=value,
    )


def test_drop_too_long_keywords():
    """OpenSearch rejects a keyword value longer than 32766 bytes."""
    # The longest value accepted, one byte per character.
    longest = "a" * MAX_KEYWORD_BYTES
    too_long = "a" * (MAX_KEYWORD_BYTES + 1)
    doc = ExtractedDocument(
        id="img-1",
        values=[_keyword("image_id", "img-1"), _keyword("dataset_id", too_long)],
        groups=[
            OpenSearchGroup(
                group="staining",
                values=[
                    _keyword("staining_target", too_long, "staining"),
                    _keyword("staining_procedure_other", longest, "staining"),
                ],
            )
        ],
    )

    logs = drop_too_long_keywords(doc)

    assert doc.values == [_keyword("image_id", "img-1")]
    assert doc.groups[0].values == [
        _keyword("staining_procedure_other", longest, "staining")
    ]
    assert logs == [
        StoredDocumentLog(
            document_id="img-1",
            field_id=field_id,
            severity="ERROR",
            message=f"Value is ignored: longer than {MAX_KEYWORD_BYTES} bytes.",
        )
        for field_id in ("dataset_id", "staining_target")
    ]


@pytest.mark.parametrize(
    ("document_scope", "scopes", "kept"),
    [
        ("other_scope", {"scope", "other_scope"}, True),
        ("other_scope", {"scope"}, False),
        # The field declares no scopes.
        ("other_scope", None, True),
        # The document has no scope.
        (None, {"scope"}, True),
    ],
)
def test_drop_out_of_scope_values(document_scope, scopes, kept):
    """drop_out_of_scope_values drops a value whose field's scopes leave out the
    document's scope, with a WARNING. A field without scopes, or a document
    without a scope, keeps its value."""
    doc = _document_with_group(["field"], document_scope)
    # A field declaring no scopes has no entry.
    field_scopes = {} if scopes is None else {"field": scopes}

    logs = drop_out_of_scope_values(doc, field_scopes)

    assert _group_field_ids(doc) == (["field"] if kept else [])
    assert logs == (
        []
        if kept
        else [
            StoredDocumentLog(
                document_id=doc.id,
                field_id="field",
                severity="WARNING",
                message="Value is ignored: the field is not indexed for scope "
                "'other_scope'.",
            )
        ]
    )


def _document_with_group(field_ids, scope=None) -> ExtractedDocument:
    """A document with one nested group item, holding a value of each field."""
    return ExtractedDocument(
        id="document",
        scope=scope,
        groups=[
            OpenSearchGroup(
                group="nested_group",
                values=[_keyword(f, "value", "nested_group") for f in field_ids],
            )
        ],
    )


def _group_field_ids(doc: ExtractedDocument) -> list[str]:
    return [value.field.id for value in doc.groups[0].values]


@pytest.mark.parametrize(
    ("field_ids", "kept"),
    [
        (["requires_field"], False),
        (["required_field", "requires_field"], True),
        # Any one of the required fields is enough.
        (["other_required_field", "requires_field"], True),
        # A field that requires nothing is kept.
        (["other_field"], True),
    ],
)
def test_drop_values_without_required(field_ids, kept):
    """drop_values_without_required drops a value of a field whose item has none
    of the fields it requires, with a WARNING. Other values are kept."""
    required_fields = {"requires_field": {"required_field", "other_required_field"}}
    doc = _document_with_group(field_ids)

    logs = drop_values_without_required(doc, required_fields)

    assert _group_field_ids(doc) == (field_ids if kept else [])
    assert logs == (
        []
        if kept
        else [
            StoredDocumentLog(
                document_id=doc.id,
                field_id="requires_field",
                severity="WARNING",
                message="Value is ignored: none of the fields it requires has a value.",
            )
        ]
    )


def _nested_term(field_id, field_type, scopes=(), requires=None):
    return OpenSearchBeaconFilteringTerm(
        id=field_id,
        type=field_type,
        nested_group="nested_group",
        scopes=list(scopes),
        label=field_id,
        description=field_id,
        ontology=BeaconFilteringOntology(id="SCTID")
        if field_type == "ontologyOrValue"
        else None,
        requires=requires,
    )


def test_indexed_field_ids():
    """_indexed_field_ids gives an ontologyOrValue term a second field <id>_other for its free text."""
    assert _indexed_field_ids(_nested_term("field", "keyword")) == {"field"}
    assert _indexed_field_ids(_nested_term("field", "ontologyOrValue")) == {
        "field",
        "field_other",
    }


def test_load_service_requires_ontology_or_value():
    """An ontologyOrValue field's free text, <id>_other, gets the field's scopes
    and requires, and counts as the field when another field requires it."""
    service = _load_service(
        filtering_terms=[
            _nested_term("required_field", "ontologyOrValue", ["scope", "other_scope"]),
            _nested_term(
                "requires_field",
                "ontologyOrValue",
                ["scope"],
                requires=["required_field"],
            ),
        ],
        filtering_scopes=(),
    )

    assert service._field_scopes == {
        "required_field": {"scope", "other_scope"},
        "required_field_other": {"scope", "other_scope"},
        "requires_field": {"scope"},
        "requires_field_other": {"scope"},
    }
    assert service._required_fields == {
        "requires_field": {"required_field", "required_field_other"},
        "requires_field_other": {"required_field", "required_field_other"},
    }


@pytest.mark.asyncio
async def test_load_initialises_once(monkeypatch):
    service = _load_service()

    reads = []
    for cache in service._term_caches.values():
        read = AsyncMock()
        monkeypatch.setattr(cache, "load", read)
        reads.append(read)
    for ontology in {b.ontology for b in service._ontology_bindings.values()}:
        read = AsyncMock()
        monkeypatch.setattr(ontology, "init", read)
        reads.append(read)

    for _ in range(3):
        await service._initialise()

    for read in reads:
        read.assert_awaited_once_with()
