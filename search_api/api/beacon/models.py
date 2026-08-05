from pydantic import BaseModel, ConfigDict, Field, model_validator
from typing import Generic, Literal, Sequence, TypeVar

BEACON_API_VERSION = "v2.0"
BEACON_ORGANISATION_ID = "fi.csc"
BEACON_ORGANISATION_NAME = "CSC – IT Center for Science"

SNOMED_ONTOLOGY_ID = "SCTID"

BeaconQueryGranularity = Literal["boolean", "count", "record"]
BeaconFilteringTermType = Literal[
    "text",
    "keyword",
    "controlledValue",
    "ontology",
    "ontologyOrValue",
    "iso8601Range",
]


# Beacon V2 query
#


# https://github.com/ga4gh-beacon/beacon-v2/blob/main/framework/json/requests/beaconRequestMeta.json
class BeaconQueryMeta(BaseModel):
    """Beacon V2 query request meta is ignored."""

    apiVersion: str = "v2.0"


# https://github.com/ga4gh-beacon/beacon-v2/blob/main/framework/json/requests/filteringTerms.json
class BeaconQueryFilter(BaseModel):
    """
    Beacon V2 query filter.

    All fields are queried using id as the field name and value as the filter value.
    Ontology fields additionally support includeDescendantTerms, which enables
    inclusion of all descendant ontology terms (e.g. SNOMED CT concepts)
    in the query results.
    """

    # Used in all Beacon V2 filters.
    id: str

    # Used in Beacon V2 AlphanumericFilter.
    value: str | list[str]
    operator: Literal["="] = "="  # Only equality operator is supported

    # Used in Beacon V2 OntologyFilter.
    includeDescendantTerms: bool = False


class BeaconQuery(BaseModel):
    """Beacon V2 query."""

    filters: list[BeaconQueryFilter] = Field(default_factory=list)
    requestedGranularity: BeaconQueryGranularity = "count"
    # Beacon V2 extension.
    requestedScope: str | None = None


# https://github.com/ga4gh-beacon/beacon-v2/blob/main/framework/json/requests/beaconRequestBody.json
class BeaconQueryRequest(BaseModel):
    """Beacon V2 query request."""

    meta: BeaconQueryMeta = BeaconQueryMeta()
    query: BeaconQuery


# Beacon V2 result
#


class BeaconResponseMeta(BaseModel):
    """Beacon V2 meta response. Does not validate against the JSON schema."""

    apiVersion: str = BEACON_API_VERSION
    beaconId: str
    returnedGranularity: BeaconQueryGranularity


# https://github.com/ga4gh-beacon/beacon-v2/blob/main/framework/json/responses/sections/beaconBooleanResponseSection.json
class BeaconResultExistsResponseSummary(BaseModel):
    """Beacon V2 result exists response summary."""

    exists: bool


# https://github.com/ga4gh-beacon/beacon-v2/blob/main/framework/json/responses/beaconBooleanResponse.json
class BeaconBooleanResponse(BaseModel):
    """Beacon V2 boolean response."""

    meta: BeaconResponseMeta
    responseSummary: BeaconResultExistsResponseSummary


# https://github.com/ga4gh-beacon/beacon-v2/blob/main/framework/json/responses/sections/beaconCountResponseSection.json
# https://github.com/ga4gh-beacon/beacon-v2/blob/main/framework/json/responses/sections/beaconSummaryResponseSection.json
class BeaconResultCountResponseSummary(BaseModel):
    """Beacon V2 result count response summary."""

    exists: bool
    numTotalResults: int


# https://github.com/ga4gh-beacon/beacon-v2/blob/main/framework/json/responses/beaconCountResponse.json
class BeaconCountResponse(BaseModel):
    """Beacon V2 count response."""

    meta: BeaconResponseMeta
    responseSummary: BeaconResultCountResponseSummary


class BeaconResultSetResult(BaseModel):
    """Beacon V2 result sets result. Schema is deployment-specific."""


R = TypeVar("R", bound=BeaconResultSetResult)


# https://github.com/ga4gh-beacon/beacon-v2/blob/main/framework/json/responses/sections/beaconResultsets.json
class BeaconResultSet(BaseModel, Generic[R]):
    """Beacon V2 result set. Parameterise with the deployment-specific result type."""

    id: str
    setType: str = "dataset"
    exists: bool = True
    results: list[R] = Field(default_factory=list)


# https://github.com/ga4gh-beacon/beacon-v2/blob/main/framework/json/responses/sections/beaconResultsets.json
class BeaconResultSets(BaseModel, Generic[R]):
    """Beacon V2 result sets response. Parameterise with the deployment-specific result type."""

    resultSet: list[BeaconResultSet[R]] = Field(default_factory=list)


# https://github.com/ga4gh-beacon/beacon-v2/blob/main/framework/json/responses/beaconResultsetsResponse.json
class BeaconResultSetsResponse(BaseModel, Generic[R]):
    """Beacon V2 result sets response. Subclass and pin R to the deployment-specific result type."""

    meta: BeaconResponseMeta
    responseSummary: BeaconResultCountResponseSummary
    response: BeaconResultSets[R]


# Beacon V2 filtering terms
#


class BeaconSchema(BaseModel):
    """Beacon V2 schema."""

    entityType: str


class BeaconInfoMeta(BaseModel):
    """Beacon V2 info meta."""

    apiVersion: str = BEACON_API_VERSION
    beaconId: str
    returnedSchemas: list[BeaconSchema]


class BeaconFilteringOntology(BaseModel):
    # Beacon V2 ontology filtering extension.

    # Reject unknown keys so config errors surface.
    model_config = ConfigDict(extra="forbid")

    id: str


class BeaconFilteringTerm(BaseModel):
    """Beacon V2 filtering term."""

    id: str
    type: BeaconFilteringTermType
    scopes: list[str]
    label: str
    # Beacon V2 extension.
    description: str
    ui_group: str | None = Field(
        default=None, description="UI group id this term belongs to."
    )
    ui_display: bool = Field(
        default=True, description="Whether to show this term in the UI."
    )
    ontology: BeaconFilteringOntology | None = Field(
        default=None,
        description="The ontology used for the field.",
    )
    ontologyConcept: str | list[str] | None = Field(
        default=None,
        description=(
            "The ontology concept(s) that bound the part of the ontology this "
            "field is searched within. Required for 'ontology' and "
            "'ontologyOrValue' fields. A single concept id covers that concept "
            "and all of its descendants, so it names a subtree; a list covers "
            "only the listed concepts, so it is an explicit set."
        ),
    )
    controlledValues: list[str] | None = None

    @property
    def snomed_ecl(self) -> str:
        """Build a SNOMED CT ECL expression from ``ontologyConcept``.

        A single concept id becomes ``<< id``, matching the concept and all of
        its descendants. A list becomes ``id OR id ...``, matching only those
        concepts and *not* their descendants.

        :raises ValueError: if the field has no ontologyConcept configured.
        """
        if isinstance(self.ontologyConcept, str):
            return f"<< {self.ontologyConcept}"
        if self.ontologyConcept:
            return " OR ".join(self.ontologyConcept)
        raise ValueError(f"No ontologyConcept configured for field '{self.id}'")

    @model_validator(mode="after")
    def validate_filtering_term(self):
        if self.type in {"ontology", "ontologyOrValue"}:
            if self.ontology is None:
                raise ValueError(
                    "ontology must be provided when type is 'ontology' or 'ontologyOrValue'"
                )
            if not self.ontologyConcept:
                raise ValueError(
                    "ontologyConcept must be provided when type is 'ontology' or 'ontologyOrValue'"
                )

        if self.type == "controlledValue" and self.controlledValues is None:
            raise ValueError(
                "controlledValues must be provided when type is 'controlledValue'"
            )

        return self


# https://github.com/ga4gh-beacon/beacon-v2/blob/main/framework/json/responses/sections/beaconFilteringTermsResults.json
class BeaconFilteringTerms(BaseModel):
    """Beacon V2 filtering terms."""

    filteringTerms: Sequence[BeaconFilteringTerm]


# https://github.com/ga4gh-beacon/beacon-v2/blob/main/framework/json/responses/beaconFilteringTermsResponse.json
class BeaconFilteringTermsResponse(BaseModel):
    """Beacon V2 filtering terms response."""

    meta: BeaconInfoMeta
    response: BeaconFilteringTerms


# Beacon V2 extension.
class BeaconFilteringGroup(BaseModel):
    """A named group that organises filtering terms in the UI."""

    model_config = ConfigDict(extra="forbid")

    id: str
    label: str
    description: str = ""


# Beacon V2 extension.
class BeaconFilteringScope(BaseModel):
    """A named scope that documents belong to and queries can be restricted to."""

    model_config = ConfigDict(extra="forbid")

    id: str
    label: str
    description: str = ""


# Beacon V2 info
#


# https://github.com/ga4gh-beacon/beacon-v2/blob/main/framework/json/responses/sections/beaconInfoResults.json
class BeaconOrganisation(BaseModel):
    """Beacon V2 organisation."""

    id: str = BEACON_ORGANISATION_ID
    name: str = BEACON_ORGANISATION_NAME


# https://github.com/ga4gh-beacon/beacon-v2/blob/main/framework/json/responses/sections/beaconInfoResults.json
class BeaconInfo(BaseModel):
    """Beacon V2 info."""

    id: str
    name: str
    apiVersion: str = BEACON_API_VERSION
    environment: Literal["prod", "test", "dev", "staging"]
    organization: BeaconOrganisation = BeaconOrganisation()


# https://github.com/ga4gh-beacon/beacon-v2/blob/main/framework/json/responses/beaconInfoResponse.json
class BeaconInfoResponse(BaseModel):
    """Beacon V2 info response."""

    meta: BeaconInfoMeta
    response: BeaconInfo
