from pydantic import BaseModel, Field


class FieldValue(BaseModel):
    value: str
    count: int = 0
    concept_id: str | None = None


class IndexedFieldValueCounts(BaseModel):
    counts: dict[str, int]
    other_counts: dict[str, int] = Field(default_factory=dict)


class AIQueryRequest(BaseModel):
    query: str
