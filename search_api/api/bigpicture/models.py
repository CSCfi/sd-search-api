from pydantic import BaseModel, Field
from typing import Any


class Filter(BaseModel):
    id: str
    value: Any


class QueryRequest(BaseModel):
    filters: list[Filter] = Field(default_factory=list)
    skip: int = 0
    limit: int = 10
    requestedGranularity: str = "record"  # or "count"


class DatasetResult(BaseModel):
    datasetId: str
    datasetTitle: str | None
    datasetDescription: str | None
    totalImageCount: int
    matchingImageCount: int
    imageIds: list[str]


class QueryResponse(BaseModel):
    response: dict[str, Any]
