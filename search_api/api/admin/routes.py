from fastapi import APIRouter, Depends, Request

from search_api.api.admin.auth import require_admin
from search_api.api.beacon.models import SNOMED_ONTOLOGY_ID, BeaconFilteringTerm
from search_api.api.models import FieldValue
from search_api.exceptions import UserException
from search_api.services.ontology_term import OntologyTermCacheService
from search_api.services.snomed import SnomedService, is_concept_id

router = APIRouter(prefix="/admin", dependencies=[Depends(require_admin)])


def _snomed_term_service(request: Request) -> OntologyTermCacheService:
    """Return the SNOMED CT term cache from app state."""
    return request.app.state.ontology_term_services[SNOMED_ONTOLOGY_ID]


@router.post("/snomed/reload", status_code=204)
async def reload_snomed_cache(request: Request) -> None:
    """Reload the in-memory SNOMED CT preferred term cache from the database."""
    await _snomed_term_service(request).load()


@router.post("/snomed/refresh", status_code=204)
async def refresh_snomed_terms(request: Request) -> None:
    """Update the SNOMED CT preferred terms stored in the database.

    Use after a SNOMED release to update preferred terms. Also, updates the
    in-memory SNOMED preferred term cache.
    """
    await _snomed_term_service(request).refresh(SnomedService())


def _get_ontology_filtering_term(
    field_id: str, request: Request
) -> BeaconFilteringTerm:
    filtering_terms: list[BeaconFilteringTerm] = request.app.state.filtering_terms
    term = next((t for t in filtering_terms if t.id == field_id), None)
    if term is None:
        raise UserException(f"Unknown field: '{field_id}'.")
    if term.type not in ("ontology", "ontologyOrValue"):
        raise UserException(
            f"Concept validation is not supported for field '{field_id}' (type '{term.type}')."
        )
    return term


async def _get_ontology_field_counts(field_id: str, request: Request) -> dict[str, int]:
    _get_ontology_filtering_term(field_id, request)
    field_counts = (
        await request.app.state.beacon_service.get_indexed_field_value_counts(field_id)
    )
    return field_counts.counts


@router.get(
    "/snomed/fields/{field_id}/invalid_concepts", response_model=list[FieldValue]
)
async def invalid_concepts(field_id: str, request: Request) -> list[FieldValue]:
    """Return values indexed for the field that are not valid SNOMED CT concept IDs."""
    counts = await _get_ontology_field_counts(field_id, request)
    results = [
        FieldValue(value=v, count=cnt)
        for v, cnt in counts.items()
        if not is_concept_id(v)
    ]
    return sorted(results, key=lambda x: x.count, reverse=True)


@router.get(
    "/snomed/fields/{field_id}/unexpected_concepts", response_model=list[FieldValue]
)
async def unexpected_concepts(field_id: str, request: Request) -> list[FieldValue]:
    """Return concept IDs indexed for the field that are absent from the SNOMED preferred term cache.

    A concept ID is considered unexpected if it was never successfully resolved via Snowstorm.
    """
    counts = await _get_ontology_field_counts(field_id, request)
    valid_format = {v: cnt for v, cnt in counts.items() if is_concept_id(v)}

    if not valid_format:
        return []

    in_cache = await _snomed_term_service(request).get_preferred_terms(
        field_id, set(valid_format.keys())
    )
    results = [
        FieldValue(value=cid, count=cnt)
        for cid, cnt in valid_format.items()
        if cid not in in_cache
    ]
    return sorted(results, key=lambda x: x.count, reverse=True)
