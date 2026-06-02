from fastapi import APIRouter, Depends, HTTPException, Query

from .models import (
    BP_BEACON_ID,
    BP_FILTERING_TERMS,
    BP_FILTERING_TERMS_RESPONSE,
    BP_INFO_RESPONSE,
    AIQueryRequest,
)
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
from .services.beacon import BigpictureBeaconService, OpenSearchBigpictureBeaconService
from .services.ai import AISearchResult, ai_search
from search_api.conf import common_config
from search_api.services.snomed import SnomedConcept, autocomplete_concepts

router = APIRouter()


def get_beacon_service() -> BigpictureBeaconService:
    cfg = common_config()
    return OpenSearchBigpictureBeaconService(
        cfg.OPENSEARCH_HOST,
        cfg.OPENSEARCH_PORT,
        cfg.OPENSEARCH_USER,
        cfg.OPENSEARCH_PASSWORD,
    )


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
    request: BeaconQueryRequest,
    backend: BigpictureBeaconService = Depends(get_beacon_service),
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


@router.post(
    "/ai/query",
    response_model=AISearchResult,
)
async def ai_query(
    request: AIQueryRequest,
    beacon_service: BigpictureBeaconService = Depends(get_beacon_service),
) -> AISearchResult:
    return await ai_search(request.query, beacon_service)


@router.get(
    "/autocomplete",
    response_model=list[SnomedConcept],
)
async def autocomplete(
    field: str = Query(description="Filtering term field ID."),
    term: str = Query(description="Partial text to search for."),
    limit: int = Query(default=10, ge=1, le=50),
    prefix_match: bool = Query(
        default=True,
        description="Use word-boundary prefix matching instead of substring matching.",
    ),
) -> list[SnomedConcept]:
    """Return SNOMED CT concept suggestions for a given ontology field and search term."""
    filtering_term = next((t for t in BP_FILTERING_TERMS if t.id == field), None)
    if filtering_term is None:
        raise HTTPException(status_code=404, detail=f"Unknown field: '{field}'.")

    ecl = filtering_term.snomed_ecl
    if ecl is None:
        raise HTTPException(status_code=400, detail=f"Unsupported field: '{field}'.")

    return await autocomplete_concepts(
        term=term, ecl=ecl, limit=limit, prefix_match=prefix_match
    )


@router.get("/health")
async def health(service: BigpictureBeaconService = Depends(get_beacon_service)):
    try:
        if await service.is_healthy():
            return {"status": "ok"}

        raise HTTPException(status_code=503, detail="unhealthy")

    except Exception as e:
        raise HTTPException(status_code=503, detail=str(e))
