from typing import Literal

from pydantic import BaseModel

from search_api.api.beacon.models import BeaconQueryFilter


class AIInterpretation(BaseModel):
    """How the AI understood a query, and the filters it recommends for it."""

    interpretation: str
    filters: list[BeaconQueryFilter]


# What a field takes: one of its values, values found with get_values, an
# ISO-8601 duration or range, or free text.
AIFieldAccepts = Literal["values", "get_values", "duration", "text"]


# Returned and explained by get_filtering_terms.
class AIFilterableField(BaseModel):
    """A field the model can filter on."""

    # The model is given field labels instead of fields ids so that the model's
    # interpretation uses field labels.
    field: str  # The field label.
    accepts: AIFieldAccepts
    values: list[str] | None = None


# AIFilter's docstring is sent to the model, as part of the answer's JSON schema.
class AIFilter(BaseModel):
    """A filter on one field. Give the field exactly as get_filtering_terms lists it."""

    field: str  # The field label.
    value: str | list[str]
    includeDescendantTerms: bool = False
