from fastapi import APIRouter, Depends
from typing import Any
import json
from pathlib import Path

from .models import QueryRequest
from .services import BigpictureBeaconService, MockBigpictureBeaconService

DEFAULT_LIMIT = 1000

router = APIRouter()


def load_json(name: str) -> dict[str, Any]:
    path = Path(__file__).parent.parent.parent / "beacon" / "bigpicture" / name
    with open(path) as f:
        return json.load(f)


# TODO: implement services
def get_service() -> BigpictureBeaconService:
    return MockBigpictureBeaconService()


#
#


@router.get("/info")
async def get_info():
    return load_json("info.json")


@router.get("/filtering_terms")
async def get_filtering_terms():
    return load_json("filtering_terms.json")


@router.post("/query")
async def query_beacon(
    request: QueryRequest, backend: BigpictureBeaconService = Depends(get_service)
):
    filters = [f.model_dump() for f in request.filters]

    if request.requestedGranularity == "count":
        result = await backend.query_datasets(
            filters=filters,
            limit=getattr(request, "limit", DEFAULT_LIMIT),
            after_key=getattr(request, "after_key", None),
        )
        result_sets = result["result_sets"]

        exists = any(rs.get("resultsCount", 0) > 0 for rs in result_sets)

        return {
            "meta": {
                "apiVersion": "v2.0",
                "receivedRequestSummary": {
                    "requestedGranularity": request.requestedGranularity,
                    "filters": filters,
                },
                "pagination": {"skip": request.skip, "limit": request.limit},
            },
            "responseSummary": {"exists": exists},
            "response": {"resultSets": result_sets},
        }
    else:
        # TODO: implement getting image ids
        raise NotImplementedError()
