"""AI search result models."""

from pydantic import BaseModel


class AIQueryFilter(BaseModel):
    id: str
    value: str | list[str]
    allowed_values: list[str] | None = None
    # TODO(improve): support ontology descendants.
    # includeDescendantTerms: bool = True


class AIDatasetResult(BaseModel):
    dataset_id: str
    dataset_title: str | None = None
    matching_image_count: int
    total_image_count: int


class AISearchResult(BaseModel):
    """Structured result returned by the AI search agent."""

    interpretation: str
    filters: list[AIQueryFilter]
    dataset_count: int
    datasets: list[AIDatasetResult]
