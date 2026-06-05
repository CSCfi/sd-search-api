import asyncio

from fastapi import APIRouter, Depends, HTTPException, Path, Query

from .models import (
    BP_BEACON_ID,
    BP_FILTERING_TERMS,
    BP_FILTERING_TERMS_RESPONSE,
    BP_INFO_RESPONSE,
    AIQueryRequest,
    FieldValueCount,
    FieldValueSuggestion,
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
from search_api.services.snomed import SnomedService

router = APIRouter()


def get_beacon_service() -> BigpictureBeaconService:
    cfg = common_config()
    return OpenSearchBigpictureBeaconService(
        cfg.OPENSEARCH_HOST,
        cfg.OPENSEARCH_PORT,
        cfg.OPENSEARCH_USER,
        cfg.OPENSEARCH_PASSWORD,
    )


def get_snomed_service() -> SnomedService:
    return SnomedService()


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
    "/filtering_terms/{field_id}/suggestions",
    response_model=list[FieldValueSuggestion],
)
async def suggestions(
    field_id: str = Path(description="Filtering term field ID."),
    term: str = Query(description="Partial text to search for."),
    word_match: bool = Query(
        default=True,
        description="Use word-boundary prefix matching instead of substring matching.",
    ),
    include_all_controlled_values: bool = Query(
        default=False,
        description="When True, include all controlled values. When False, only include indexed values.",
    ),
    include_all_ontology_values: bool = Query(
        default=True,
        description="When True, search all concepts in the ontology field's hierarchy using text and synonym matching. "
        "When False, search only indexed concepts. Preferred terms are returned in both cases.",
    ),
    include_other_ontology_values: bool = Query(
        default=True,
        description="When True, include free-text ontology field values.",
    ),
    beacon_service: BigpictureBeaconService = Depends(get_beacon_service),
    snomed_service: SnomedService = Depends(get_snomed_service),
) -> list[FieldValueSuggestion]:
    """Return value suggestions for a given field and search term."""
    filtering_term = next((t for t in BP_FILTERING_TERMS if t.id == field_id), None)
    if filtering_term is None:
        raise HTTPException(status_code=404, detail=f"Unknown field: '{field_id}'.")

    if filtering_term.type not in ("controlledValue", "ontology", "ontologyOrValue"):
        raise HTTPException(
            status_code=400,
            detail=f"Suggestions are not supported for field '{field_id}' (type '{filtering_term.type}').",
        )

    def _matches(value: str) -> bool:
        value_lower = value.lower()
        term_lower = term.lower()
        if word_match:
            return any(word.startswith(term_lower) for word in value_lower.split())
        return term_lower in value_lower

    if filtering_term.type == "controlledValue":
        if include_all_controlled_values:
            # Match against all controlled values.
            candidates = filtering_term.controlledValues or []
        else:
            # Match against indexed controlled values.
            field_counts = await beacon_service.get_indexed_field_value_counts(field_id)
            candidates = list(field_counts[0].keys())
        matched = sorted(v for v in candidates if _matches(v))
        return [FieldValueSuggestion(term=v) for v in matched]

    field_counts = await beacon_service.get_indexed_field_value_counts(field_id)

    if include_all_ontology_values:
        # Match against all ontology concepts.
        snomed_results = await snomed_service.suggest_concepts(
            term=term,
            ecl=filtering_term.snomed_ecl,
        )
        results = [
            FieldValueSuggestion(term=s.preferred_term, concept_id=s.concept_id)
            for s in snomed_results
            if _matches(s.preferred_term)
        ]
    else:
        # Match against indexed ontology concepts.
        preferred_terms = await snomed_service.get_preferred_terms(
            set(field_counts[0].keys()), filtering_term.snomed_ecl
        )
        matches = sorted(
            (preferred_term, concept_id)
            for concept_id, preferred_term in preferred_terms.items()
            if _matches(preferred_term)
        )
        results = [
            FieldValueSuggestion(term=preferred_term, concept_id=concept_id)
            for preferred_term, concept_id in matches
        ]

    if filtering_term.type == "ontology":
        return results

    if filtering_term.type == "ontologyOrValue" and include_other_ontology_values:
        # Match against free-text ontology values.
        existing = {s.term for s in results}
        for text_value in sorted(field_counts[1]):
            if _matches(text_value) and text_value not in existing:
                results.append(FieldValueSuggestion(term=text_value))

    return results


@router.get(
    "/filtering_terms/{field_id}/values",
    response_model=list[FieldValueCount],
)
async def values(
    field_id: str = Path(description="Filtering term field ID."),
    include_all_controlled_values: bool = Query(
        default=False,
        description="When True, include all controlled values with count 0 for unindexed values. "
        "When False, only include indexed values.",
    ),
    include_all_ontology_values: bool = Query(
        default=True,
        description="When True, return all concepts for the ontology field's hierarchy "
        "When False, return only indexed concepts. Preferred terms are returned in both cases.",
    ),
    include_other_ontology_values: bool = Query(
        default=True,
        description="When True, include free-text ontology field values.",
    ),
    beacon_service: BigpictureBeaconService = Depends(get_beacon_service),
    snomed_service: SnomedService = Depends(get_snomed_service),
) -> list[FieldValueCount]:
    """Return the values for a given field, ordered by count."""
    filtering_term = next((t for t in BP_FILTERING_TERMS if t.id == field_id), None)
    if filtering_term is None:
        raise HTTPException(status_code=404, detail=f"Unknown field: '{field_id}'.")

    if filtering_term.type not in ("controlledValue", "ontology", "ontologyOrValue"):
        raise HTTPException(
            status_code=400,
            detail=f"Values are not supported for field '{field_id}' (type '{filtering_term.type}').",
        )

    field_counts = await beacon_service.get_indexed_field_value_counts(field_id)
    counts = field_counts[0]

    if filtering_term.type == "controlledValue":
        if include_all_controlled_values:
            all_values = filtering_term.controlledValues or []
            sorted_values = sorted(
                ((v, counts.get(v, 0)) for v in all_values),
                key=lambda x: x[1],
                reverse=True,
            )
        else:
            sorted_values = sorted(counts.items(), key=lambda x: x[1], reverse=True)
        return [FieldValueCount(value=v, count=c) for v, c in sorted_values]

    if include_all_ontology_values:
        preferred_terms = await snomed_service.get_preferred_terms(
            None, filtering_term.snomed_ecl
        )
        results: list[tuple[str, int, str | None]] = [
            (preferred_term, counts.get(concept_id, 0), concept_id)
            for concept_id, preferred_term in preferred_terms.items()
        ]
    else:
        preferred_terms = await snomed_service.get_preferred_terms(
            set(counts.keys()), filtering_term.snomed_ecl
        )
        results = [
            (preferred_terms.get(concept_id, concept_id), count, concept_id)
            for concept_id, count in counts.items()
        ]

    if filtering_term.type == "ontologyOrValue" and include_other_ontology_values:
        results += [
            (text_value, count, None) for text_value, count in field_counts[1].items()
        ]

    sorted_results = sorted(results, key=lambda x: x[1], reverse=True)
    return [
        FieldValueCount(value=label, count=count, concept_id=concept_id)
        for label, count, concept_id in sorted_results
    ]


@router.get("/health")
async def health(service: BigpictureBeaconService = Depends(get_beacon_service)):
    try:
        if await service.is_healthy():
            return {"status": "ok"}

        raise HTTPException(status_code=503, detail="unhealthy")

    except Exception as e:
        raise HTTPException(status_code=503, detail=str(e))
