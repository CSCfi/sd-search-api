from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class FieldValue(BaseModel):
    value: str
    count: int = 0
    concept_id: str | None = None


class ValueCounts(BaseModel):
    counts: dict[str, int]
    other_counts: dict[str, int] = Field(default_factory=dict)


class DocumentCounts(BaseModel):
    """How many documents are indexed, and how many are waiting to be.

    ``indexed`` is counted in the search index and ``pending`` in the document store,
    so the two come from different stores and neither includes the other.
    """

    indexed: int
    pending: int


class ScopedCounts(BaseModel):
    """What one scope holds."""

    documents: DocumentCounts


class DeploymentStatus(BaseModel):
    """What a deployment holds, in total and per scope.

    Every document carries one of the deployment's declared scopes, so the scopes
    account for all of ``documents``. A deployment declaring no scope reports none,
    and only the totals say what it holds.
    """

    deployment: str
    documents: DocumentCounts
    scopes: dict[str, ScopedCounts]
    last_indexed: datetime | None = None


class ValueCountsKey(BaseModel):
    model_config = ConfigDict(frozen=True)

    field_id: str
    scope: str | None = None

    @classmethod
    def of(cls, field_id: str, scope: str | None = None) -> "ValueCountsKey":
        return cls(field_id=field_id, scope=scope)


class AIQueryRequest(BaseModel):
    query: str
