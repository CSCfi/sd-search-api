from pydantic import BaseModel, Field, model_validator
from typing import Literal

BEACON_API_VERSION = "v2.0"
BEACON_ORGANISATION_ID = "fi.csc"
BEACON_ORGANISATION_NAME = "CSC – IT Center for Science"

SNOMED_ONTOLOGY_ID = "SCTID"

BeaconQueryGranularity = Literal["boolean", "count", "record"]
BeaconFilteringTermType = Literal[
    "text",
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
    # TODO(improve): support ontology descendants.
    includeDescendantTerms: bool = True


class BeaconQuery(BaseModel):
    """Beacon V2 query."""

    filters: list[BeaconQueryFilter] = Field(default_factory=list)
    requestedGranularity: BeaconQueryGranularity = "count"


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
    """Beacon V2 result sets result. Not constrained by a JSON schema."""

    datasetId: str
    datasetTitle: str | None
    datasetDescription: str | None
    totalImageCount: int
    matchingImageCount: int
    imageIds: list[str] = Field(default_factory=list)


# https://github.com/ga4gh-beacon/beacon-v2/blob/main/framework/json/responses/sections/beaconResultsets.json
class BeaconResultSet(BaseModel):
    """Beacon V2 result set."""

    id: str
    setType: str = "dataset"
    exists: bool = True
    results: list[BeaconResultSetResult]


# https://github.com/ga4gh-beacon/beacon-v2/blob/main/framework/json/responses/sections/beaconResultsets.json
class BeaconResultSets(BaseModel):
    """Beacon V2 result sets response."""

    resultSet: list[BeaconResultSet] = Field(default_factory=list)


# https://github.com/ga4gh-beacon/beacon-v2/blob/main/framework/json/responses/beaconResultsetsResponse.json
class BeaconResultSetsResponse(BaseModel):
    """Beacon V2 result sets response."""

    meta: BeaconResponseMeta
    responseSummary: BeaconResultCountResponseSummary
    response: BeaconResultSets


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

    id: str
    rootTerms: list[str] | None = None
    allowedTerms: list[str] | None = None


class BeaconFilteringTerm(BaseModel):
    """Beacon V2 filtering term."""

    id: str
    type: BeaconFilteringTermType
    scopes: list[str]
    label: str
    # Beacon V2 extension.
    description: str
    ontology: BeaconFilteringOntology | None = Field(
        default=None,
        description="The ontology used for the field.",
    )
    ontologyConcept: str | list[str] | None = Field(
        default=None,
        description="A single ontology concept ID including descendants or a list of concept IDs.",
    )
    controlledValues: list[str] | None = None

    @property
    def snomed_ecl(self) -> str | None:
        """Build a SNOMED CT ECL expression from ontology concepts."""
        if self.ontologyConcept is None:
            return None
        if isinstance(self.ontologyConcept, str):
            return f"<< {self.ontologyConcept}"
        return " OR ".join(self.ontologyConcept) if self.ontologyConcept else None

    @model_validator(mode="after")
    def validate_filtering_term(self):
        if self.type in {"ontology", "ontologyOrValue"} and self.ontology is None:
            raise ValueError(
                "ontology must be provided when type is 'ontology' or 'ontologyOrValue'"
            )

        if self.type == "controlledValue" and self.controlledValues is None:
            raise ValueError(
                "controlledValues must be provided when type is 'controlledValue'"
            )

        return self


# https://github.com/ga4gh-beacon/beacon-v2/blob/main/framework/json/responses/sections/beaconFilteringTermsResults.json
class BeaconFilteringTerms(BaseModel):
    """Beacon V2 filtering terms."""

    filteringTerms: list[BeaconFilteringTerm]


# https://github.com/ga4gh-beacon/beacon-v2/blob/main/framework/json/responses/beaconFilteringTermsResponse.json
class BeaconFilteringTermsResponse(BaseModel):
    """Beacon V2 filtering terms response."""

    meta: BeaconInfoMeta
    response: BeaconFilteringTerms


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
