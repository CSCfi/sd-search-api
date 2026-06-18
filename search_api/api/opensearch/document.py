from collections.abc import Sequence
from typing import Any

from search_api.api.opensearch.models import OpenSearchFieldType, OpenSearchFieldValue
from search_api.api.opensearch.services import iso8601_duration_to_days


def _encode_value(field_type: OpenSearchFieldType, value: Any) -> Any:
    if field_type == "iso8601Range":
        return {
            "gte": iso8601_duration_to_days(value[0]),
            "lte": iso8601_duration_to_days(value[1]),
        }
    return value


def build_document(values: Sequence[OpenSearchFieldValue]) -> dict[str, Any]:
    """Build a nested OpenSearch document from field values.

    The ``opensearch_field`` path determines where the value is written:
    - No dot: placed directly in the root dict. The ``index`` is ignored.
    - Dotted path (``root.field``): the first segment names a nested array and the
      ``index`` selects the element within the array.  The remaining segments are plain
      nested objects within that element.

    For ``multivalued`` fields, successive values for the same ``opensearch_field`` path
    and ``index`` are appended to a list rather than overwriting.
    """

    def _assign(
        target: dict[str, Any], key: str, value: Any, multivalued: bool
    ) -> None:
        if multivalued:
            target.setdefault(key, []).append(value)
        else:
            target[key] = value

    document: dict[str, Any] = {}
    for v in values:
        path = v.field.opensearch_field
        encoded = _encode_value(v.field.type, v.value)
        root_path, _, sub_path = path.partition(".")
        if not sub_path:
            _assign(document, root_path, encoded, v.field.multivalued)
            continue
        array = document.setdefault(root_path, [])
        while len(array) <= v.index:
            array.append({})
        node = array[v.index]
        *segments, leaf = sub_path.split(".")
        for segment in segments:
            node = node.setdefault(segment, {})
        _assign(node, leaf, encoded, v.field.multivalued)
    return document
