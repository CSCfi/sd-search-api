from fastapi import APIRouter, Depends

from .models import BP_BEACON_ID, BP_FILTERING_TERMS_RESPONSE, BP_INFO_RESPONSE
from ..beacon.models import (
    BeaconQueryRequest,
    BeaconBooleanResponse,
    BeaconCountResponse,
    BeaconResultSetsResponse,
    BeaconResponseMeta,
    BeaconResultCountResponseSummary,
    BeaconResultExistsResponseSummary,
    BeaconFilteringTermsResponse,
    BeaconInfoResponse,
)
from .services import BigpictureBeaconService, OpenSearchBigpictureBeaconService

router = APIRouter()

# TODO(improve): use environmental variables
OPENSEARCH_HOST = "localhost"
OPENSEARCH_PORT = 9200


def get_service() -> BigpictureBeaconService:
    return OpenSearchBigpictureBeaconService(OPENSEARCH_HOST, OPENSEARCH_PORT)


@router.get(
    "/info",
    response_model=BeaconInfoResponse,
    response_model_exclude_none=True,
)
async def info() -> BeaconInfoResponse:
    return BP_INFO_RESPONSE


@router.get(
    "/filtering_terms",
    response_model=BeaconFilteringTermsResponse,
    response_model_exclude_none=True,
)
async def filtering_terms() -> BeaconFilteringTermsResponse:
    return BP_FILTERING_TERMS_RESPONSE


@router.post(
    "/query",
    response_model=(
        BeaconBooleanResponse | BeaconCountResponse | BeaconResultSetsResponse
    ),
    response_model_exclude_none=True,
)
async def query_beacon(
    request: BeaconQueryRequest, backend: BigpictureBeaconService = Depends(get_service)
) -> BeaconBooleanResponse | BeaconCountResponse | BeaconResultSetsResponse:
    response = await backend.query(
        filters=request.query.filters,
    )

    meta = BeaconResponseMeta(
        returnedGranularity=request.query.requestedGranularity, beaconId=BP_BEACON_ID
    )

    if request.query.requestedGranularity == "boolean":
        return BeaconBooleanResponse(
            meta=meta,
            responseSummary=BeaconResultExistsResponseSummary(
                exists=len(response.resultSet) > 0
            ),
        )

    if request.query.requestedGranularity == "count":
        return BeaconCountResponse(
            meta=meta,
            responseSummary=BeaconResultCountResponseSummary(
                exists=len(response.resultSet) > 0,
                numTotalResults=len(response.resultSet),
            ),
        )

    return BeaconResultSetsResponse(
        meta=meta,
        responseSummary=BeaconResultCountResponseSummary(
            exists=len(response.resultSet) > 0, numTotalResults=len(response.resultSet)
        ),
        response=response,
    )
