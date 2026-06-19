"""Ontology provider abstraction and registry.

An ontology provider resolves coded values (concept IDs) against a terminology
service. Providers are selected per filtering term by that term's ``ontology.id``
(e.g. ``SCTID`` -> SNOMED CT).

Each provider module self-registers via :func:`register_ontology_service` at
import time.
"""

from abc import ABC, abstractmethod
from collections.abc import Sequence

from search_api.api.beacon.models import BeaconFilteringTerm, BeaconQueryFilter
from search_api.exceptions import SystemException


class OntologyService(ABC):
    """Resolves coded values for one ontology against its terminology service."""

    @abstractmethod
    def is_concept_id(self, value: str) -> bool:
        """Return True if value is a concept ID in this ontology."""

    @abstractmethod
    async def get_preferred_terms(self, concept_ids: set[str]) -> dict[str, str]:
        """Return preferred terms for concept IDs. IDs not found are omitted."""

    @abstractmethod
    async def prepare_ontology_filter(
        self,
        query_filter: BeaconQueryFilter,
        filtering_terms: Sequence[BeaconFilteringTerm],
    ) -> BeaconQueryFilter:
        """Resolve, and optionally expand, a filter's values to concept IDs."""


_PROVIDERS: dict[str, "OntologyService"] = {}


def register_ontology_service(ontology_id: str, service: OntologyService) -> None:
    """Register the provider for an ontology id (e.g. ``SCTID``).

    Called by each provider module at import time.
    """
    _PROVIDERS[ontology_id] = service


def get_ontology_service(ontology_id: str) -> OntologyService:
    """Return the provider registered for an ontology id.

    :raises SystemException: if no provider is registered for the id, e.g. when
        the provider module has not been imported.
    """
    try:
        return _PROVIDERS[ontology_id]
    except KeyError:
        raise SystemException(
            f"No ontology provider registered for ontology id {ontology_id!r}. "
            f"Registered: {', '.join(sorted(_PROVIDERS)) or '(none)'}."
        )
