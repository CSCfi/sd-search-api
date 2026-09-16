from fastapi import APIRouter, Depends, Request

from search_api.api.admin.auth import require_admin
from search_api.api.beacon.models import SNOMED_ONTOLOGY_ID
from search_api.services.ontology.term_cache import OntologyTermCache
from search_api.services.ontology.snomed import SnomedService

router = APIRouter(prefix="/admin", dependencies=[Depends(require_admin)])


def _snomed_term_service(request: Request) -> OntologyTermCache:
    """Return the SNOMED CT term cache from app state."""
    return request.app.state.ontology_term_services[SNOMED_ONTOLOGY_ID]


@router.post("/caches/reload", status_code=204)
async def reload_caches(request: Request) -> None:
    """Reload the in-memory term caches from the database."""
    for term_cache in request.app.state.ontology_term_services.values():
        await term_cache.load()
    await request.app.state.value_counts.refresh()


@router.post("/snomed/refresh", status_code=204)
async def refresh_snomed_terms(request: Request) -> None:
    """Update the SNOMED CT preferred terms stored in the database.

    Use after a SNOMED release to update preferred terms. Also, updates the
    in-memory SNOMED preferred term cache.
    """
    await _snomed_term_service(request).refresh(SnomedService())
