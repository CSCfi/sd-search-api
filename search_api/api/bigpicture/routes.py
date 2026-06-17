import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request

from search_api.exceptions import SystemException, UserException

from search_api.api.bigpicture.models import (
    BP_BEACON_ID,
    BP_FILTERING_TERMS,
    BP_FILTERING_TERMS_RESPONSE,
    BP_INFO_RESPONSE,
    BP_ONTOLOGY_FILTERING_TERMS,
    BP_OPENSEARCH_INDEX,
    BigpictureBeaconResultSetResult,
    BigpictureBeaconResultSetsResponse,
)
from search_api.api.models import AIQueryRequest, FieldValue
from search_api.api.beacon.models import (
    BeaconQueryRequest,
    BeaconBooleanResponse,
    BeaconCountResponse,
    BeaconResponseMeta,
    BeaconResultCountResponseSummary,
    BeaconResultExistsResponseSummary,
    BeaconFilteringTermsResponse,
    BeaconInfoResponse,
)
from search_api.api.beacon.services import BeaconService
from search_api.api.opensearch.models import OpenSearchBeaconFilteringTerm
from search_api.api.bigpicture.opensearch import BigpictureOpenSearchBeaconService
from search_api.ai.models import AISearchResult
from search_api.ai.services import AIService
from search_api.conf import feature_config
from search_api.services.snomed import SnomedService
from search_api.services.snomed_term import SnomedTermCacheService

logger = logging.getLogger(__name__)

router = APIRouter()


def get_beacon_service(
    request: Request,
) -> BeaconService[OpenSearchBeaconFilteringTerm, BigpictureBeaconResultSetResult]:
    return BigpictureOpenSearchBeaconService(
        request.app.state.search,
        BP_OPENSEARCH_INDEX,
        BP_FILTERING_TERMS,
    )


def get_ai_service() -> AIService:
    return AIService(BP_FILTERING_TERMS)


def get_snomed_service() -> SnomedService:
    return SnomedService()


def get_snomed_term_service(request: Request) -> SnomedTermCacheService:
    return request.app.state.snomed_term_service


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
        BeaconBooleanResponse | BeaconCountResponse | BigpictureBeaconResultSetsResponse
    ),
    response_model_exclude_none=True,
)
async def query(
    request: BeaconQueryRequest,
    beacon_service: BeaconService = Depends(get_beacon_service),
    snomed_service: SnomedService = Depends(get_snomed_service),
) -> BeaconBooleanResponse | BeaconCountResponse | BigpictureBeaconResultSetsResponse:
    ontology_field_ids = {t.id for t in BP_ONTOLOGY_FILTERING_TERMS}
    ontology_filters = [f for f in request.query.filters if f.id in ontology_field_ids]
    other_filters = [f for f in request.query.filters if f.id not in ontology_field_ids]

    # Resolve ontology filter values to concept IDs, and optionally expand to descendants.
    try:
        resolved_ontology_filters = list(
            await asyncio.gather(
                *[
                    snomed_service.prepare_ontology_filter(f, BP_FILTERING_TERMS)
                    for f in ontology_filters
                ]
            )
        )
    except Exception as e:
        raise SystemException("Ontology service error.") from e

    granularity = request.query.requestedGranularity
    filters = other_filters + resolved_ontology_filters
    response = await beacon_service.query(filters=filters, granularity=granularity)
    num_results = len(response.resultSet)
    exists = num_results > 0
    meta = BeaconResponseMeta(returnedGranularity=granularity, beaconId=BP_BEACON_ID)

    if granularity == "boolean":
        return BeaconBooleanResponse(
            meta=meta,
            responseSummary=BeaconResultExistsResponseSummary(exists=exists),
        )

    if granularity == "count":
        return BeaconCountResponse(
            meta=meta,
            responseSummary=BeaconResultCountResponseSummary(
                exists=exists,
                numTotalResults=num_results,
            ),
        )

    if granularity == "record":
        return BigpictureBeaconResultSetsResponse(
            meta=meta,
            responseSummary=BeaconResultCountResponseSummary(
                exists=exists, numTotalResults=num_results
            ),
            response=response,
        )

    raise UserException(f"Unsupported granularity: {granularity!r}")


if feature_config().FEATURE_AI:

    @router.post(
        "/ai/query",
        response_model=AISearchResult,
    )
    async def ai_query(
        request: AIQueryRequest,
        beacon_service: BeaconService = Depends(get_beacon_service),
        ai_service: AIService = Depends(get_ai_service),
    ) -> AISearchResult:
        return await ai_service.search(request.query, beacon_service)


@router.get(
    "/filtering_terms/{field_id}/suggestions",
    response_model=list[FieldValue],
)
async def suggestions(
    field_id: str = Path(description="Filtering term field ID."),
    term: str = Query(description="Partial text to search for."),
    substring_match: bool = Query(
        default=False,
        description="Use substring matching instead of word-boundary prefix matching.",
    ),
    include_all_controlled_values: bool = Query(
        default=False,
        description="When True, include all controlled values. When False, only include indexed values.",
    ),
    include_other_ontology_values: bool = Query(
        default=True,
        description="When True, include free-text ontology field values.",
    ),
    beacon_service: BeaconService = Depends(get_beacon_service),
    snomed_term_service: SnomedTermCacheService = Depends(get_snomed_term_service),
) -> list[FieldValue]:
    """Return value suggestions for a given field and search term."""
    filtering_term = next((t for t in BP_FILTERING_TERMS if t.id == field_id), None)
    if filtering_term is None:
        raise UserException(f"Unknown field: '{field_id}'.")

    if filtering_term.type not in (
        "controlledValue",
        "keyword",
        "ontology",
        "ontologyOrValue",
    ):
        raise UserException(
            f"Suggestions are not supported for field '{field_id}' (type '{filtering_term.type}')."
        )

    def _matches(value: str) -> bool:
        value_lower = value.lower()
        term_lower = term.lower()
        if substring_match:
            return term_lower in value_lower
        return any(word.startswith(term_lower) for word in value_lower.split())

    if filtering_term.type in ("controlledValue", "keyword"):
        field_counts = await beacon_service.get_indexed_field_value_counts(field_id)
        counts = field_counts.counts
        if filtering_term.type == "controlledValue" and include_all_controlled_values:
            candidates = filtering_term.controlledValues or []
        else:
            candidates = list(counts.keys())
        return [
            FieldValue(value=v, count=counts.get(v, 0))
            for v in sorted(v for v in candidates if _matches(v))
        ]

    field_counts = await beacon_service.get_indexed_field_value_counts(field_id)
    counts = field_counts.counts
    preferred_terms = await snomed_term_service.get_preferred_terms(
        field_id, set(counts.keys())
    )
    results = [
        FieldValue(
            value=preferred_term, concept_id=concept_id, count=counts[concept_id]
        )
        for preferred_term, concept_id in sorted(
            (preferred_term, concept_id)
            for concept_id, preferred_term in preferred_terms.items()
            if _matches(preferred_term)
        )
    ]

    if filtering_term.type == "ontology":
        return results

    if filtering_term.type == "ontologyOrValue" and include_other_ontology_values:
        existing = {s.value for s in results}
        for text_value, count in field_counts.other_counts.items():
            if _matches(text_value) and text_value not in existing:
                results.append(FieldValue(value=text_value, count=count))

    return results


@router.get(
    "/filtering_terms/{field_id}/values",
    response_model=list[FieldValue],
)
async def values(
    field_id: str = Path(description="Filtering term field ID."),
    include_all_controlled_values: bool = Query(
        default=False,
        description="When True, include all controlled values with count 0 for unindexed values. "
        "When False, only include indexed values.",
    ),
    include_other_ontology_values: bool = Query(
        default=True,
        description="When True, include free-text ontology field values.",
    ),
    beacon_service: BeaconService = Depends(get_beacon_service),
    snomed_term_service: SnomedTermCacheService = Depends(get_snomed_term_service),
) -> list[FieldValue]:
    """Return the values for a given field, ordered by count."""
    filtering_term = next((t for t in BP_FILTERING_TERMS if t.id == field_id), None)
    if filtering_term is None:
        raise UserException(f"Unknown field: '{field_id}'.")

    if filtering_term.type not in (
        "controlledValue",
        "keyword",
        "ontology",
        "ontologyOrValue",
    ):
        raise UserException(
            f"Values are not supported for field '{field_id}' (type '{filtering_term.type}')."
        )

    field_counts = await beacon_service.get_indexed_field_value_counts(field_id)
    counts = field_counts.counts

    if filtering_term.type in ("controlledValue", "keyword"):
        if filtering_term.type == "controlledValue" and include_all_controlled_values:
            all_values = filtering_term.controlledValues or []
            sorted_values = sorted(
                ((v, counts.get(v, 0)) for v in all_values),
                key=lambda x: x[1],
                reverse=True,
            )
        else:
            sorted_values = sorted(counts.items(), key=lambda x: x[1], reverse=True)
        return [FieldValue(value=v, count=c) for v, c in sorted_values]

    preferred_terms = await snomed_term_service.get_preferred_terms(
        field_id, set(counts.keys())
    )
    results: list[tuple[str, int, str | None]] = [
        (preferred_term, counts[concept_id], concept_id)
        for concept_id, preferred_term in preferred_terms.items()
    ]

    if filtering_term.type == "ontologyOrValue" and include_other_ontology_values:
        results += [
            (text_value, count, None)
            for text_value, count in field_counts.other_counts.items()
        ]

    sorted_results = sorted(results, key=lambda x: x[1], reverse=True)
    return [
        FieldValue(value=label, count=count, concept_id=concept_id)
        for label, count, concept_id in sorted_results
    ]


@router.get("/health")
async def health(service: BeaconService = Depends(get_beacon_service)):
    try:
        healthy = await service.is_healthy()
    except Exception as e:
        raise SystemException("Health check failed.") from e
    if not healthy:
        raise HTTPException(status_code=503, detail="unhealthy")
    return {"status": "ok"}
