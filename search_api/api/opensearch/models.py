from typing import Literal

from pydantic import BaseModel, Field, model_validator

from search_api.api.beacon.models import BeaconFilteringTerm

OpenSearchFieldType = Literal["keyword", "text", "long", "integer_range"]


class OpenSearchFieldMapping(BaseModel):
    """OpenSearch mapping for a single leaf index field."""

    type: OpenSearchFieldType
    analyzer: str | None = None


class OpenSearchOntologyOrValue(BaseModel):
    """OpenSearch field mapping for ontologyOrValue filtering terms.

    Concept IDs are routed to ``concept_value_field``
    while other values are routed to ``other_value_field``.
    """

    concept_value_field: str
    other_value_field: str


class OpenSearchBeaconFilteringTerm(BeaconFilteringTerm):
    """Beacon filtering term with an associated OpenSearch field mapping.

    ``opensearch_field`` is excluded from API serialisation so it never
    appears in ``/filtering_terms`` responses.
    """

    opensearch_field: str | OpenSearchOntologyOrValue = Field(
        exclude=True,
        description="The OpenSearch field(s) to query for this term.",
    )

    @model_validator(mode="after")
    def validate_opensearch_field(self) -> "OpenSearchBeaconFilteringTerm":
        if self.type == "ontologyOrValue" and not isinstance(
            self.opensearch_field, OpenSearchOntologyOrValue
        ):
            raise ValueError(
                f"Field '{self.id}' has type 'ontologyOrValue' so opensearch_field "
                f"must be OpenSearchOntologyOrValue."
            )
        return self
