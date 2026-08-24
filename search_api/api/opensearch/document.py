from typing import Any

from search_api.api.opensearch.models import (
    ExtractedDocument,
    OpenSearchFieldValue,
    OpenSearchGroup,
)
from search_api.api.opensearch.clauses import iso8601_duration_to_days
from search_api.api.scopes import SCOPE_FIELD


def _encode_value(field_value: OpenSearchFieldValue) -> Any:
    field_type = field_value.field.type
    value: Any = field_value.value
    if field_type == "iso8601Range":
        return {
            "gte": iso8601_duration_to_days(value[0]),
            "lte": iso8601_duration_to_days(value[1]),
        }
    if field_type in ("ontology", "ontologyOrValue"):
        # The concept id the load resolved the ontology value to.
        return field_value.resolved_concept_id or value[0]
    return value


def _add_field_value(target: dict[str, Any], value: OpenSearchFieldValue) -> None:
    """Add field value into the target object.

    target is the document root for a top-level field, and a group item for a
    field in a group. E.g.:

        sex=Female  ->  {"sex": "Female"}

    A multivalued field appends instead of overwriting, so its values accumulate
    into a list::

        anatomical_site=80248007,368209003  ->  {"anatomical_site": ["80248007", "368209003"]}
    """
    encoded = _encode_value(value)
    if encoded is None:
        return
    if value.field.multivalued:
        target.setdefault(value.field.id, []).append(encoded)
    else:
        target[value.field.id] = encoded


def _build_group_item(group: OpenSearchGroup) -> dict[str, Any]:
    """Build one item of a nested group."""
    item: dict[str, Any] = {}
    for value in group.values:
        _add_field_value(item, value)
    return item


def build_document(document: ExtractedDocument) -> dict[str, Any]:
    """Build an OpenSearch document from an extracted document.

    The scope is written at the document root, followed by
     top level values and nested groups, e.g.:

        {"scope": "clinical",
         "image_id": "img-1",
         "observation": [{"diagnosis": "73211009", "observation_type": "confirmed"}]}
    """
    result: dict[str, Any] = {}

    # Scope.
    if document.scope is not None:
        result[SCOPE_FIELD] = document.scope

    # Top level fields.
    for value in document.values:
        _add_field_value(result, value)

    for group in document.groups:
        item = _build_group_item(group)
        if item:
            result.setdefault(group.group, []).append(item)

    return result
