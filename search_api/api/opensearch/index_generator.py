"""Generate an OpenSearch index body from OpenSearch field definitions."""

from collections.abc import Sequence
from typing import Any

from search_api.api.opensearch.models import (
    OpenSearchField,
    OpenSearchOntologyOrValue,
)

# The text analyzer referenced by ``text`` fields and defined in the index settings.
_TEXT_ANALYZER = "english_text"

# Index settings shared by every generated index.
_SETTINGS: dict[str, Any] = {
    "analysis": {
        "analyzer": {
            _TEXT_ANALYZER: {"type": "standard", "stopwords": "_english_"},
        }
    }
}

# Maps a semantic field type to its OpenSearch field mapping. ``ontologyOrValue``
# is handled separately because it spans two fields.
_TYPE_MAPPING: dict[str, dict[str, Any]] = {
    "text": {"type": "text", "analyzer": _TEXT_ANALYZER},
    "keyword": {"type": "keyword"},
    "controlledValue": {"type": "keyword"},
    "ontology": {"type": "keyword"},
    "iso8601Range": {"type": "integer_range"},
    "integer": {"type": "long"},
}


class OpenSearchIndexGeneratorService:
    """Generate an OpenSearch index body from a list of OpenSearch field definitions."""

    def __init__(self, fields: Sequence[OpenSearchField]) -> None:
        self._fields = fields

    def generate(self) -> dict[str, Any]:
        """Return the full OpenSearch index body with settings and mappings."""
        properties: dict[str, Any] = {}
        for field in self._fields:
            for path, mapping in self._field_mappings(field):
                self._insert(properties, path, mapping)
        return {"settings": _SETTINGS, "mappings": {"properties": properties}}

    @staticmethod
    def _field_mappings(field: OpenSearchField) -> list[tuple[str, dict[str, Any]]]:
        """Return (field_path, mapping) pairs for a field."""
        path = field.opensearch_field
        if isinstance(path, OpenSearchOntologyOrValue):
            # Both the concept and the free-text field are exact-match keywords.
            return [
                (path.concept_value_field, {"type": "keyword"}),
                (path.other_value_field, {"type": "keyword"}),
            ]
        mapping = _TYPE_MAPPING.get(field.type)
        if mapping is None:
            raise ValueError(
                f"No OpenSearch mapping for field '{field.id}' (type '{field.type}')."
            )
        return [(path, mapping)]

    @staticmethod
    def _insert(properties: dict[str, Any], path: str, mapping: dict[str, Any]) -> None:
        """Insert a field mapping at a dotted path, creating nested containers as needed.

        Every segment before the leaf becomes a ``nested`` container, so ``blocks.foo``
        nests one level and ``blocks.foo.bar`` nests two. A path with no dot is a
        top-level field.
        """
        *containers, field = path.split(".")
        cursor = properties
        for container in containers:
            nested = cursor.setdefault(container, {"type": "nested", "properties": {}})
            cursor = nested["properties"]
        cursor[field] = mapping
