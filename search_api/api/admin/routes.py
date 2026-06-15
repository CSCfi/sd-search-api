from fastapi import APIRouter, Depends, Request

from search_api.api.admin.auth import require_admin
from search_api.services.snomed import SnomedService

router = APIRouter(prefix="/admin", dependencies=[Depends(require_admin)])


@router.post("/snomed/reload", status_code=204)
async def reload_snomed_cache(request: Request) -> None:
    """Reload the in-memory SNOMED CT preferred term cache from the database."""
    await request.app.state.snomed_term_service.load()


@router.post("/snomed/refresh", status_code=204)
async def refresh_snomed_terms(request: Request) -> None:
    """Update the SNOMED CT preferred terms stored in the database.

    Use after a SNOMED release to update preferred terms. Also, updates the
    in-memory SNOMED preferred term cache.
    """
    await request.app.state.snomed_term_service.refresh(SnomedService())
