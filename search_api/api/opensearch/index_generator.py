"""Generate an OpenSearch index body from Beacon filtering terms."""

from collections.abc import Sequence
from typing import Any

from search_api.api.opensearch.models import (
    OpenSearchBeaconFilteringTerm,
    OpenSearchFieldMapping,
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

# Maps a Beacon filtering term type to its OpenSearch field mapping. ``ontologyOrValue``
# is handled separately because it spans two fields.
_TYPE_MAPPING: dict[str, OpenSearchFieldMapping] = {
    "text": OpenSearchFieldMapping(type="text", analyzer=_TEXT_ANALYZER),
    "keyword": OpenSearchFieldMapping(type="keyword"),
    "controlledValue": OpenSearchFieldMapping(type="keyword"),
    "ontology": OpenSearchFieldMapping(type="keyword"),
    "iso8601Range": OpenSearchFieldMapping(type="integer_range"),
}


class OpenSearchIndexGeneratorService:
    """Generate an OpenSearch index body from Beacon filtering terms.

    Field names and types are derived from the filtering terms — the single source
    of truth — so the index mapping cannot drift from them. Fields that are indexed
    for retrieval but are not filterable, and therefore have no filtering term, are
    supplied separately via ``non_filtering_fields``.
    """

    def __init__(
        self,
        filtering_terms: Sequence[OpenSearchBeaconFilteringTerm],
        non_filtering_fields: dict[str, OpenSearchFieldMapping] | None = None,
    ) -> None:
        self._filtering_terms = filtering_terms
        self._non_filtering_fields = non_filtering_fields or {}

    def generate(self) -> dict[str, Any]:
        """Return the full OpenSearch index body (settings + mappings)."""
        properties: dict[str, Any] = {
            name: mapping.model_dump(exclude_none=True)
            for name, mapping in self._non_filtering_fields.items()
        }
        for term in self._filtering_terms:
            for path, mapping in self._field_mappings(term):
                self._insert(properties, path, mapping.model_dump(exclude_none=True))
        return {"settings": _SETTINGS, "mappings": {"properties": properties}}

    @staticmethod
    def _field_mappings(
        term: OpenSearchBeaconFilteringTerm,
    ) -> list[tuple[str, OpenSearchFieldMapping]]:
        """Return (field_path, mapping) pairs for a filtering term."""
        field = term.opensearch_field
        if isinstance(field, OpenSearchOntologyOrValue):
            # Both the concept and the free-text field are exact-match keywords.
            return [
                (field.concept_value_field, OpenSearchFieldMapping(type="keyword")),
                (field.other_value_field, OpenSearchFieldMapping(type="keyword")),
            ]
        mapping = _TYPE_MAPPING.get(term.type)
        if mapping is None:
            raise ValueError(
                f"No OpenSearch mapping for filtering term '{term.id}' (type '{term.type}')."
            )
        return [(field, mapping)]

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
