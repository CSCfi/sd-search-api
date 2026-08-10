from abc import ABC, abstractmethod
from search_api.services.ontology.cache.models import CachedOntology


class OntologySource(ABC):
    """A source for an ontology."""

    @abstractmethod
    async def fetch(self) -> CachedOntology:
        """Fetch the latest ontology version."""

    @abstractmethod
    def is_newer(self, version: str, other: str) -> bool:
        """Return True if version is newer than other.

        A version string is only meaningful to the source that produced
        it, so each source implements its version comparison.
        """
