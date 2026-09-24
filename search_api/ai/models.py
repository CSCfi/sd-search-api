from typing import Generic, TypeVar

from pydantic import BaseModel

from search_api.api.beacon.models import (
    BeaconBooleanResponse,
    BeaconCountResponse,
    BeaconQueryFilter,
    BeaconResultSetsResponse,
)


class AIInterpretation(BaseModel):
    """How the AI understood a query, and the filters it chose for it."""

    interpretation: str
    filters: list[BeaconQueryFilter]


T = TypeVar("T", bound=BeaconResultSetsResponse)


class AISearchResponse(AIInterpretation, Generic[T]):
    """The Beacon V2 response for the chosen filters, at the requested granularity."""

    result: BeaconBooleanResponse | BeaconCountResponse | T
