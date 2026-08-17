from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

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

ONTOLOGY_OTHER_VALUE_FIELD_SUFFIX = "_other"


class OpenSearchOntologyOrValue(BaseModel):
    """The two physical OpenSearch fields backing an ontologyOrValue term.

    Concept IDs are routed to ``concept_value_field``
    while other values are routed to ``other_value_field``.
    """

    concept_value_field: str
    other_value_field: str


class OpenSearchField(BaseModel):
    """A field that is indexed in OpenSearch and searchable.

    The indexed path is ``<group>.<id>`` when ``group`` names a nested container
    (e.g. ``blocks`` ), or just ``<id>`` for a top-level field.
    """

    # Reject unknown keys so config typos surface as errors.
    model_config = ConfigDict(extra="forbid")

    id: str
    type: OpenSearchFieldType

    # Excluded from responses.
    group: str | None = Field(default=None, exclude=True)
    multivalued: bool = Field(default=False, exclude=True)

    @property
    def opensearch_field(self) -> str:
        """The full indexed path: ``<group>.<id>``, or ``id`` at the top level."""
        return f"{self.group}.{self.id}" if self.group else self.id

    # Indexed path is <group>.<id>, with no dots allowed in <group> or <id>.

    @field_validator("id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        if "." in value:
            raise ValueError(f"Field id '{value}' contains a dot.")
        return value

    @field_validator("group")
    @classmethod
    def validate_group(cls, value: str | None) -> str | None:
        if value is not None and "." in value:
            raise ValueError(f"Field group '{value}' contains a dot.")
        return value


class OpenSearchBeaconFilteringTerm(BeaconFilteringTerm, OpenSearchField):
    """Beacon filtering term."""

    # Reject unknown keys so config typos surface as errors.
    model_config = ConfigDict(extra="forbid")

    @property
    def opensearch_field(self) -> str | OpenSearchOntologyOrValue:  # type: ignore[override]
        # ontologyOrValue terms span two physical fields: the concept-id field
        # (<group>.<id>) and the free-text field (<group>.<id>_other).
        base = super().opensearch_field
        if self.type == "ontologyOrValue":
            return OpenSearchOntologyOrValue(
                concept_value_field=base,
                other_value_field=f"{base}{ONTOLOGY_OTHER_VALUE_FIELD_SUFFIX}",
            )
        return base

    @model_validator(mode="after")
    def _validate_ontology_or_value(self) -> "OpenSearchBeaconFilteringTerm":
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
    "ontology": tuple,  # (concept id, meaning)
    "ontologyOrValue": tuple,  # (concept id, meaning)
    "iso8601Range": tuple,
    "integer": int,
}


class OpenSearchFieldValue(BaseModel):
    """An OpenSearch field value."""

    model_config = ConfigDict(frozen=True)

    field: OpenSearchField
    value: str | int | tuple[str | None, str | None]
    # The concept id an ontology field value was resolved to.
    resolved_concept_id: str | None = None

    @model_validator(mode="after")
    def validate_value(self) -> "OpenSearchFieldValue":
        expected = _VALUE_TYPES[self.field.type]
        if not isinstance(self.value, expected):
            raise ValueError(
                f"Value for field '{self.field.id}' (type '{self.field.type}') "
                f"must be {expected}."
            )
        return self


class OpenSearchGroup(BaseModel):
    """An OpenSearch nested group."""

    group: str
    values: list[OpenSearchFieldValue]
    qualifiers: dict[str, str] = Field(default_factory=dict)  # qualifier id -> value

    @model_validator(mode="after")
    def validate_group(self) -> "OpenSearchGroup":
        misplaced = sorted(
            value.field.id for value in self.values if value.field.group != self.group
        )
        if misplaced:
            raise ValueError(
                f"Field(s) {', '.join(misplaced)} are not in group '{self.group}'."
            )
        return self


class ExtractedDocument(BaseModel):
    """One extracted OpenSearch document.

    The ``values`` are top-level fields, while the ``groups`` are nested groups.
    """

    id: str
    scope: str | None = None
    values: list[OpenSearchFieldValue] = Field(default_factory=list)
    groups: list[OpenSearchGroup] = Field(default_factory=list)
    modified_at: datetime | None = None

    @property
    def all_values(self) -> list[OpenSearchFieldValue]:
        return [*self.values, *(v for group in self.groups for v in group.values)]
