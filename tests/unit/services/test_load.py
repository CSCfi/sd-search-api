"""Boundary validation of extracted documents against the deployment's config."""

import pytest

from search_api.api.bigpicture.domain import BP_DOMAIN
from search_api.api.opensearch.models import (
    ExtractedDocument,
    OpenSearchField,
    OpenSearchFieldValue,
    OpenSearchGroup,
)
from search_api.exceptions import UserException
from search_api.services.load import LoadService
from search_api.services.ontology.term_cache import create_term_caches


def _load_service(**overrides) -> LoadService:
    kwargs = {
        "term_caches": create_term_caches(BP_DOMAIN.ontology_ids),
        "filtering_terms": BP_DOMAIN.filtering_terms,
        "filtering_scopes": BP_DOMAIN.filtering_scopes,
        "filtering_qualifiers": BP_DOMAIN.filtering_qualifiers,
    }
    kwargs.update(overrides)
    return LoadService(**kwargs)


def _document(scope="clinical", qualifiers=None) -> ExtractedDocument:
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
                qualifiers=qualifiers or {},
            )
        ],
    )


def test_validate_document_accepts_declared_scope_and_qualifiers():
    _load_service().validate_document(
        _document(qualifiers={"observation": "confirmed"})
    )


def test_validate_document_accepts_no_qualifiers():
    """A group item need not carry any qualifier value."""
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


def test_validate_document_rejects_unknown_qualifier():
    with pytest.raises(UserException, match="Unsupported qualifier: 'certainty'"):
        _load_service().validate_document(_document(qualifiers={"certainty": "known"}))


def test_validate_document_rejects_undeclared_qualifier_value():
    with pytest.raises(UserException, match=r"Unsupported value\(s\) \['known'\]"):
        _load_service().validate_document(
            _document(qualifiers={"observation": "known"})
        )


def test_validate_document_names_the_offending_document():
    with pytest.raises(UserException, match="Document 'img-1'"):
        _load_service().validate_document(_document(scope="preclinical"))
