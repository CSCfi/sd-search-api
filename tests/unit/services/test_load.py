"""Boundary validation of extracted documents against the deployment's config."""

from unittest.mock import AsyncMock

import pytest

from search_api.api.bigpicture.domain import BP_DOMAIN
from search_api.api.opensearch.models import (
    MAX_DOCUMENT_ID_BYTES,
    MAX_KEYWORD_BYTES,
    ExtractedDocument,
    OpenSearchField,
    OpenSearchFieldValue,
    OpenSearchGroup,
)
from search_api.database.models import StoredDocumentLog
from search_api.exceptions import SystemException, UserException
from search_api.services.load import LoadService, drop_too_long_keywords
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
