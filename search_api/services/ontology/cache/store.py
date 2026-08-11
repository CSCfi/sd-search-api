"""Where a cached ontology is stored."""

import logging
from datetime import datetime

from search_api.database.models import StoredOntology
from search_api.database.ontology_cache import (
    read_ontology,
    read_updated_at,
    write_ontology,
)
from search_api.services.ontology.cache.models import (
    CachedOntology,
    CachedOntologyConcept,
)

logger = logging.getLogger(__name__)


class OntologyCacheStore:
    """Stores one ontology's concepts in the ontology_cache table."""

    def __init__(self, ontology_id: str) -> None:
        self._ontology_id = ontology_id

    @property
    def ontology_id(self) -> str:
        """The ontology this store holds."""
        return self._ontology_id

    async def read(self) -> CachedOntology | None:
        stored = await read_ontology(self._ontology_id)
        if stored is None:
            return None
        return CachedOntology(
            version=stored.version,
            sha256=stored.sha256,
            concepts=[
                CachedOntologyConcept.model_validate(concept)
                for concept in stored.concepts
            ],
        )

    async def updated_at(self) -> datetime | None:
        return await read_updated_at(self._ontology_id)

    async def write(self, fetched: CachedOntology) -> None:
        await write_ontology(
            self._ontology_id,
            StoredOntology(
                version=fetched.version,
                sha256=fetched.sha256,
                concepts=[
                    concept.model_dump(mode="json") for concept in fetched.concepts
                ],
            ),
        )
        logger.info(
            "Stored %d concept(s) for ontology '%s' version '%s' sha256 '%s'.",
            len(fetched.concepts),
            self._ontology_id,
            fetched.version,
            fetched.sha256,
        )
