import asyncio

from fastapi import APIRouter, Depends, HTTPException, Path, Query

from .models import (
    BP_BEACON_ID,
    BP_FILTERING_TERMS,
    BP_FILTERING_TERMS_RESPONSE,
    BP_INFO_RESPONSE,
    AIQueryRequest,
    FieldValueCount,
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
from search_api.services.snomed import SnomedConcept, SnomedService

router = APIRouter()


def get_beacon_service() -> BigpictureBeaconService:
    cfg = common_config()
    return OpenSearchBigpictureBeaconService(
        cfg.OPENSEARCH_HOST,
        cfg.OPENSEARCH_PORT,
        cfg.OPENSEARCH_USER,
        cfg.OPENSEARCH_PASSWORD,
    )


def get_snomed_service(
    beacon_service: BigpictureBeaconService = Depends(get_beacon_service),
) -> SnomedService:
    return SnomedService(beacon_service)


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
    snomed_service: SnomedService = Depends(get_snomed_service),
) -> BeaconBooleanResponse | BeaconCountResponse | BeaconResultSetsResponse:
    expanded_filters = list(
        await asyncio.gather(
            *[
                snomed_service.expand_ontology_filter(f, BP_FILTERING_TERMS)
                for f in request.query.filters
            ]
        )
    )
    response = await backend.query(
        filters=expanded_filters,
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
    "/fields/{field_id}/suggestions",
    response_model=list[SnomedConcept],
)
async def suggestions(
    field_id: str = Path(description="Filtering term field ID."),
    term: str = Query(description="Partial text to search for."),
    limit: int = Query(default=10, ge=1, le=50),
    prefix_match: bool = Query(
        default=True,
        description="Use word-boundary prefix matching instead of substring matching.",
    ),
    snomed_service: SnomedService = Depends(get_snomed_service),
) -> list[SnomedConcept]:
    """Return concept suggestions for a given ontology field and search term."""
    filtering_term = next((t for t in BP_FILTERING_TERMS if t.id == field_id), None)
    if filtering_term is None:
        raise HTTPException(status_code=404, detail=f"Unknown field: '{field_id}'.")

    ecl = filtering_term.snomed_ecl
    if ecl is None:
        raise HTTPException(status_code=400, detail=f"Unsupported field: '{field_id}'.")

    return await snomed_service.suggest_concepts(
        term=term, field_id=field_id, ecl=ecl, limit=limit, prefix_match=prefix_match
    )


@router.get(
    "/fields/{field_id}/values",
    response_model=list[FieldValueCount],
)
async def values(
    field_id: str = Path(description="Filtering term field ID."),
    limit: int = Query(default=10, ge=1, le=1000),
    beacon_service: BigpictureBeaconService = Depends(get_beacon_service),
    snomed_service: SnomedService = Depends(get_snomed_service),
) -> list[FieldValueCount]:
    """Return the most common indexed values for a field, ordered by count."""
    counts = await beacon_service.get_indexed_value_counts(field_id)
    if counts is None:
        raise HTTPException(status_code=400, detail=f"Unsupported field: '{field_id}'.")

    filtering_term = next((t for t in BP_FILTERING_TERMS if t.id == field_id), None)
    ecl = filtering_term.snomed_ecl if filtering_term is not None else None

    if ecl is not None:
        preferred_terms = await snomed_service.get_preferred_terms(
            set(counts.keys()), ecl
        )
        sorted_counts = sorted(counts.items(), key=lambda x: x[1], reverse=True)[:limit]
        return [
            FieldValueCount(
                value=preferred_terms.get(concept_id, concept_id),
                count=count,
                concept_id=concept_id,
            )
            for concept_id, count in sorted_counts
        ]

    sorted_values = sorted(counts.items(), key=lambda x: x[1], reverse=True)[:limit]
    return [FieldValueCount(value=v, count=c) for v, c in sorted_values]


@router.get("/health")
async def health(service: BigpictureBeaconService = Depends(get_beacon_service)):
    try:
        if await service.is_healthy():
            return {"status": "ok"}

        raise HTTPException(status_code=503, detail="unhealthy")

    except Exception as e:
        raise HTTPException(status_code=503, detail=str(e))
