from typing import Any

from search_api.api.opensearch.models import (
    ExtractedDocument,
    OpenSearchFieldType,
    OpenSearchFieldValue,
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


def _qualifier_values(field_values: list[OpenSearchFieldValue]) -> list[str]:
    """Return the encoded qualifier values of one nested item.

    Given the field values of one finding item::

        [
         finding=C3137,            qualifiers={"observation": ["confirmed"]},
         finding_severity=C147501, qualifiers={"observation": ["confirmed"]}
        ]

    returns ``["observation:confirmed"]``.

    A nested item's qualifiers are repeated on each of its field values.
    These are collapsed and sorted so a document's payload is deterministic.
    """
    qualifiers: set[str] = set()
    for field_value in field_values:
        for qualifier_id, qualifier_values in field_value.qualifiers.items():
            for qualifier_value in qualifier_values:
                qualifiers.add(encode_qualifier_value(qualifier_id, qualifier_value))
    return sorted(qualifiers)


def build_document(document: ExtractedDocument) -> dict[str, Any]:
    """Build an OpenSearch document from an extracted document.

    The ``scope`` is written at the document root.

    The ``opensearch_field`` path determines where each value is written:
    - No dot: placed directly in the root dict. The ``index`` is ignored.
    - Dotted path (``root.field``): the first segment names a nested array and the
      ``index`` selects the element within the array.  The remaining segments are plain
      nested objects within that element.

    For ``multivalued`` fields, successive values for the same ``opensearch_field`` path
    and ``index`` are appended to a list rather than overwriting.

    For nested fields with qualifiers, the qualifier ids and values are
    written to the ''qualifiers'' field in the nested group.
    """

    def _assign(
        target: dict[str, Any], key: str, value: Any, multivalued: bool
    ) -> None:
        if multivalued:
            target.setdefault(key, []).append(value)
        else:
            target[key] = value

    result: dict[str, Any] = {}

    # Add document scope.
    if document.scope is not None:
        result[SCOPE_FIELD] = document.scope

    # A nested item is identified by its (group, index). Nested item's values are
    # collected here to merge qualifiers later.
    values_by_item: dict[tuple[str, int], list[OpenSearchFieldValue]] = {}

    # Add field values.
    for field_value in document.values:
        path = field_value.field.opensearch_field
        encoded = _encode_value(field_value.field.type, field_value.value)
        if "." not in path:
            # A top-level field: the whole path is the field id.
            _assign(result, path, encoded, field_value.field.multivalued)
            continue
        # A field in a group: the first segment names the group's array.
        group, _, sub_path = path.partition(".")
        array = result.setdefault(group, [])
        while len(array) <= field_value.index:
            array.append({})
        node = array[field_value.index]
        *segments, leaf = sub_path.split(".")
        for segment in segments:
            node = node.setdefault(segment, {})
        _assign(node, leaf, encoded, field_value.field.multivalued)
        values_by_item.setdefault((group, field_value.index), []).append(field_value)

    # Add qualifier values.
    for (group, index), field_values in values_by_item.items():
        qualifiers = _qualifier_values(field_values)
        if qualifiers:
            result[group][index][QUALIFIERS_FIELD] = qualifiers

    return result
