from pydantic import BaseModel


class FieldValueSuggestion(BaseModel):
    term: str
    concept_id: str | None = None


class FieldValueCount(BaseModel):
    value: str
    count: int
    concept_id: str | None = None


class AIQueryRequest(BaseModel):
    query: str
