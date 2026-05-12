from pydantic import BaseModel, Field
from typing import Any, Literal

BeaconQueryGranularity = Literal["boolean", "count", "record"]


# Beacon V2 query
#


# https://github.com/ga4gh-beacon/beacon-v2/blob/main/framework/json/requests/beaconRequestMeta.json
class BeaconQueryMeta(BaseModel):
    """Beacon V2 query request meta is ignored."""

    apiVersion: str = "v2.0"


# https://github.com/ga4gh-beacon/beacon-v2/blob/main/framework/json/requests/filteringTerms.json
class BeaconQueryFilter(BaseModel):
    """Beacon V2 query filter based on AlphanumericFilter and OntologyFilter. Does not validate against the JSON schema."""

    id: str

    # AlphanumericFilter
    value: Any
    operator: Literal["="] = "="  # Only equality operator is supported

    # TODO(improve): support ontology descendant extension.
    # OntologyFilter
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

    apiVersion: str = "v2.0"
    beaconId: str = "fi.csc.bigpicture.beacon.v2"
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
