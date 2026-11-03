from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from search_api.severity import LogSeverity


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


class UnsyncedDocument(BaseModel):
    """A document table row pending sync."""

    model_config = ConfigDict(frozen=True)

    id: str
    payload: dict[str, Any]
    # The row version when it was read for optimistic locking.
    version: str


class StoredDocumentLog(BaseModel):
    """A document_log table row."""

    model_config = ConfigDict(frozen=True)

    document_id: str
    severity: LogSeverity
    message: str
    field_id: str | None = None
    created_at: datetime | None = None
