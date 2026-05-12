from fastapi import APIRouter, Depends
from typing import Any
import json
from pathlib import Path

from .models import (
    BeaconQueryRequest,
    BeaconBooleanResponse,
    BeaconCountResponse,
    BeaconResultSetsResponse,
    BeaconResponseMeta,
    BeaconResultCountResponseSummary,
    BeaconResultExistsResponseSummary,
)
from .services import BigpictureBeaconService, MockBigpictureBeaconService

router = APIRouter()


def get_service() -> BigpictureBeaconService:
    return MockBigpictureBeaconService()


def load_json(name: str) -> dict[str, Any]:
    path = Path(__file__).parent.parent.parent / "beacon" / "bigpicture" / name
    with open(path) as f:
        return json.load(f)


@router.get("/info")
async def get_info():
    return load_json("info.json")


@router.get("/filtering_terms")
async def get_filtering_terms():
    return load_json("filtering_terms.json")


@router.post(
    "/query",
    response_model=(
        BeaconBooleanResponse | BeaconCountResponse | BeaconResultSetsResponse
    ),
)
async def query_beacon(
    request: BeaconQueryRequest, backend: BigpictureBeaconService = Depends(get_service)
) -> BeaconBooleanResponse | BeaconCountResponse | BeaconResultSetsResponse:
    response = await backend.query(
        filters=request.query.filters,
    )

    meta = BeaconResponseMeta(returnedGranularity=request.query.requestedGranularity)

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
