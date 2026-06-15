from pydantic import BaseModel


class FieldValue(BaseModel):
    value: str
    count: int = 0
    concept_id: str | None = None


class AIQueryRequest(BaseModel):
    query: str
