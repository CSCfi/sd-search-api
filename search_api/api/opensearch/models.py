from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from search_api.api.beacon.models import BeaconFilteringTerm

# Semantic field type. The index generator maps them to a concrete OpenSearch field type.
OpenSearchFieldType = Literal[
    "text",
    "keyword",
    "controlledValue",
    "ontology",
    "ontologyOrValue",
    "iso8601Range",
    "integer",
]


class OpenSearchOntologyOrValue(BaseModel):
    """OpenSearch field mapping for ontologyOrValue filtering terms.

    Concept IDs are routed to ``concept_value_field``
    while other values are routed to ``other_value_field``.
    """

    concept_value_field: str
    other_value_field: str


class OpenSearchField(BaseModel):
    """A field that is indexed in OpenSearch and searchable."""

    id: str
    type: OpenSearchFieldType

    # Excluded from /filtering_terms responses.
    opensearch_field: str = Field(exclude=True)
    multivalued: bool = Field(default=False, exclude=True)


class OpenSearchBeaconFilteringTerm(BeaconFilteringTerm, OpenSearchField):
    """Beacon filtering term."""

    # Excluded from /filtering_terms responses.
    opensearch_field: str | OpenSearchOntologyOrValue = Field(exclude=True)  # type: ignore[assignment]

    @model_validator(mode="after")
    def validate_opensearch_field(self) -> "OpenSearchBeaconFilteringTerm":
        if self.type == "ontologyOrValue" and not isinstance(
            self.opensearch_field, OpenSearchOntologyOrValue
        ):
            raise ValueError(
                f"Field '{self.id}' has type 'ontologyOrValue' so opensearch_field "
                f"type must be OpenSearchOntologyOrValue."
            )
        if self.type == "ontologyOrValue" and self.multivalued:
            raise ValueError(
                f"Field '{self.id}' has type 'ontologyOrValue' which does not support multivalued=True."
            )
        return self


# Python value type expected for each semantic field type.
_VALUE_TYPES: dict[str, type | tuple[type, ...]] = {
    "text": str,
    "keyword": str,
    "controlledValue": str,
    "ontology": str,
    "ontologyOrValue": str,
    "iso8601Range": tuple,
    "integer": int,
}


class OpenSearchFieldValue(BaseModel):
    """An extracted value for an OpenSearch field.

    The index defines to which element for a multi-valued field the
    value belongs to.
    """

    field: OpenSearchField
    value: str | int | tuple[str, str]
    index: int = 0

    @model_validator(mode="after")
    def validate_value(self) -> "OpenSearchFieldValue":
        expected = _VALUE_TYPES[self.field.type]
        if not isinstance(self.value, expected):
            raise ValueError(
                f"Value for field '{self.field.id}' (type '{self.field.type}') "
                f"must be {expected}."
            )
        return self


class ExtractedDocument(BaseModel):
    """One extracted OpenSearch document.

    The loader builds a JSONB payload from the values.
    """

    id: str
    modified_at: datetime | None = None
    values: list[OpenSearchFieldValue]
