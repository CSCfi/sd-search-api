from pydantic import BaseModel

from search_api.api.beacon.models import BeaconQueryFilter


class AIInterpretation(BaseModel):
    """How the AI understood a query, and the filters it recommends for it."""

    interpretation: str
    filters: list[BeaconQueryFilter]
