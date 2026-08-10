from pydantic import BaseModel, ConfigDict


class CachedOntologyConcept(BaseModel):
    """A cached concept."""

    model_config = ConfigDict(frozen=True)

    concept_id: str
    preferred_term: str
    synonyms: frozenset[str] = frozenset()
    parent_ids: frozenset[str] = frozenset()


class CachedOntology(BaseModel):
    """A cached ontology.

    ``version`` is the ontology's version or date.
    ``sha256`` is the hash of the fetched content.
    """

    version: str
    sha256: str
    concepts: list[CachedOntologyConcept]
