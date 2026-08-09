from typing import Any

from search_api.api.opensearch.models import (
    ExtractedDocument,
    OpenSearchFieldType,
    OpenSearchFieldValue,
    OpenSearchGroup,
)
from search_api.api.opensearch.services import iso8601_duration_to_days
from search_api.api.qualifiers import QUALIFIERS_FIELD, encode_qualifier_value
from search_api.api.scopes import SCOPE_FIELD


def _encode_value(field_type: OpenSearchFieldType, value: Any) -> Any:
    if field_type == "iso8601Range":
        return {
            "gte": iso8601_duration_to_days(value[0]),
            "lte": iso8601_duration_to_days(value[1]),
        }
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
    encoded = _encode_value(value.field.type, value.value)
    if value.field.multivalued:
        target.setdefault(value.field.id, []).append(encoded)
    else:
        target[value.field.id] = encoded


def _build_group_item(group: OpenSearchGroup) -> dict[str, Any]:
    """Build one item of a nested group.

    The item's qualifiers go into one multivalued field, each value carrying its
    qualifier id, sorted so a document's payload is deterministic.
    """
    item: dict[str, Any] = {}
    for value in group.values:
        _add_field_value(item, value)
    qualifiers = sorted(
        encode_qualifier_value(qualifier_id, qualifier_value)
        for qualifier_id, qualifier_value in group.qualifiers.items()
    )
    if qualifiers:
        item[QUALIFIERS_FIELD] = qualifiers
    return item


def build_document(document: ExtractedDocument) -> dict[str, Any]:
    """Build an OpenSearch document from an extracted document.

    The scope is written at the document root, followed by
     top level values and nested groups, e.g.:

        {"scope": "clinical",
         "image_id": "img-1",
         "diagnosis": [{"diagnosis": "73211009",
                        "qualifiers": ["observation:confirmed"]}]}
    """
    result: dict[str, Any] = {}

    # Scope.
    if document.scope is not None:
        result[SCOPE_FIELD] = document.scope

    # Top level fields.
    for value in document.values:
        _add_field_value(result, value)

    # Nested groups.
    for group in document.groups:
        result.setdefault(group.group, []).append(_build_group_item(group))

    return result
