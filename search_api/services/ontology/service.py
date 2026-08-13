import asyncio
from abc import ABC, abstractmethod
from collections.abc import Sequence
from typing import Protocol

from search_api.api.beacon.models import BeaconFilteringTerm, BeaconQueryFilter
from search_api.exceptions import SystemException


class TermCache(Protocol):
    """Cache mapping terms to concept IDs."""

    async def get_concept_ids_by_term(self, field_id: str, term: str) -> set[str]: ...


def normalise_term(value: str) -> str:
    """Normalise a term so that it matches regardless of case and spacing."""
    return " ".join(value.split()).casefold()


class OntologyService(ABC):
    """Resolves coded values for one ontology against its terminology service."""

    @abstractmethod
    def is_concept_id(self, value: str) -> bool:
        """Return True if value is a concept ID in this ontology."""

    @abstractmethod
    async def get_preferred_terms(self, concept_ids: set[str]) -> dict[str, str]:
        """Return preferred terms for concept IDs. IDs not found are omitted."""

    async def replacement_concept_id(self, concept_id: str) -> str | None:
        """Return the active concept that replaces an inactive one, if there is one.

        None when the concept is unknown, active, has no replacement,
        or when the ontology does not record replacements.
        """
        return None

    @abstractmethod
    async def _find_concept_ids(
        self, value: str, filtering_term: BeaconFilteringTerm
    ) -> set[str]:
        """Query the ontology for one filter value's concept id(s)."""

    @abstractmethod
    async def _find_descendant_ids(self, concept_ids: set[str]) -> set[str]:
        """Query the ontology for concept id(s)' descendant concept id(s)."""

    async def _resolve_concept_ids(
        self,
        value: str,
        filtering_term: BeaconFilteringTerm,
        term_cache: TermCache | None,
    ) -> set[str]:
        """Resolve one filter value to concept id(s) in the following order:

        1. If the value is a concept id then it is returned as given
        2. If the value is cached for the field then all associated concept id(s)
           are returned
        3. Otherwise, the value is resolved against the ontology and any associated
           concept id(s) are returned.
        """
        if self.is_concept_id(value):
            return {value}

        if term_cache is not None:
            cached = await term_cache.get_concept_ids_by_term(filtering_term.id, value)
            if cached:
                return cached

        return await self._find_concept_ids(value, filtering_term)

    async def prepare_ontology_filter(
        self,
        query_filter: BeaconQueryFilter,
        filtering_terms: Sequence[BeaconFilteringTerm],
        term_cache: TermCache | None = None,
    ) -> BeaconQueryFilter:
        """Resolve filter's values to concept IDs.

        Values that do not resolve are kept only for ``ontologyOrValue`` fields.
        """

        filtering_term = next(
            (t for t in filtering_terms if t.id == query_filter.id), None
        )
        if filtering_term is None or filtering_term.type not in (
            "ontology",
            "ontologyOrValue",
        ):
            return query_filter

        values = (
            query_filter.value
            if isinstance(query_filter.value, list)
            else [query_filter.value]
        )

        resolved_ids = await asyncio.gather(
            *(self._resolve_concept_ids(v, filtering_term, term_cache) for v in values)
        )
        unresolved = [v for v, ids in zip(values, resolved_ids) if not ids]

        concept_ids: set[str] = set()
        for ids in resolved_ids:
            concept_ids.update(ids)
        if query_filter.includeDescendantTerms:
            concept_ids.update(await self._find_descendant_ids(concept_ids))
        prepared_values: list[str] = list(concept_ids)
        # Only "ontologyOrValue" has a free-text field to match them against.
        if filtering_term.type == "ontologyOrValue":
            prepared_values += unresolved

        return query_filter.model_copy(update={"value": prepared_values})

    async def init(self) -> None:
        """Perform any startup initialisation"""

    async def start(self) -> None:
        """Start a background task."""

    def stop(self) -> None:
        """Stop a background task."""


def get_ontology_id_by_field(
    filtering_terms: Sequence[BeaconFilteringTerm],
) -> dict[str, str]:
    """Map each ontology filtering term's id to its ontology id (e.g. ``SCTID``).

    The single source of truth for "which fields are ontology fields and which
    ontology each resolves against".
    :raises SystemException: if an ontology-typed term has no ontology configured.
    """
    result: dict[str, str] = {}
    for term in filtering_terms:
        if term.type in ("ontology", "ontologyOrValue"):
            if term.ontology is None:
                raise SystemException(
                    f"Filtering term '{term.id}' has no ontology configured."
                )
            result[term.id] = term.ontology.id
    return result


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
