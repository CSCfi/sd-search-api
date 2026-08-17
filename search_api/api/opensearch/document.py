from typing import Any

from search_api.api.opensearch.models import (
    ExtractedDocument,
    OpenSearchFieldValue,
    OpenSearchGroup,
)
from search_api.api.opensearch.services import iso8601_duration_to_days
from search_api.api.qualifiers import QUALIFIERS_FIELD, encode_qualifier_value
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
        # Only an ontology value encodes to None: no ontology the field accepts coded
        # it, and the load's fallback to its meaning named no concept either, so there
        # is no concept id to index. Writing it would put a null in a keyword field —
        # or [null] in a multivalued one. A load has already dropped such a value
        # (LoadService._drop_unresolved_ontology_values); this is what keeps the rule
        # for callers that build a document without loading it.
        return
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

    # A nested group is skipped if it holds nothing but its qualifiers,
    # which _build_group_item always write.
    for group in document.groups:
        item = _build_group_item(group)
        if any(key != QUALIFIERS_FIELD for key in item):
            result.setdefault(group.group, []).append(item)

    return result
