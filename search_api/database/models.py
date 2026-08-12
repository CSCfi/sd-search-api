from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

LogSeverity = Literal["WARNING", "ERROR"]


class StoredTerm(BaseModel):
    """A terms_cache table row."""

    model_config = ConfigDict(frozen=True)

    field_id: str
    concept_id: str
    preferred_term: str


class StoredOntology(BaseModel):
    """An ontology_cache table row."""

    version: str
    sha256: str
    concepts: list[dict[str, Any]]


class StoredDocumentLog(BaseModel):
    """A document_log table row."""

    model_config = ConfigDict(frozen=True)

    document_id: str
    severity: LogSeverity
    message: str
    field_id: str | None = None
    created_at: datetime | None = None
