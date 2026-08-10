from typing import Any

from pydantic import BaseModel, ConfigDict


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
