from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Generic, TypeVar

from search_api.api.beacon.models import (
    BeaconFilteringTerm,
    BeaconQueryFilter,
    BeaconQueryGranularity,
    BeaconResultSetResult,
    BeaconResultSets,
)
from search_api.api.models import ValueCounts
from search_api.exceptions import UserException

T = TypeVar("T", bound=BeaconFilteringTerm)
S = TypeVar("S", bound=BeaconResultSetResult)


class BeaconService(ABC, Generic[T]):
    """Beacon V2 endpoints except queries."""

    def __init__(self, filtering_terms: Sequence[T]) -> None:
        self.filtering_terms = filtering_terms

    def get_term(self, field_id: str) -> T:
        for term in self.filtering_terms:
            if term.id == field_id:
                return term
        raise UserException(f"Unknown field: '{field_id}'.")

    @abstractmethod
    async def count_indexed(self, scope: str | None = None) -> int:
        """Return how many documents are indexed, all of them or in a scope."""

    @abstractmethod
    async def is_healthy(self) -> bool:
        pass

    @abstractmethod
    async def get_value_counts(
        self,
        field_id: str,
        scope: str | None = None,
        qualifiers: Mapping[str, Sequence[str]] | None = None,
    ) -> ValueCounts:
        """Return value counts for the indexed fields mapped to field_id.

        For simple fields, only ``counts`` is populated.
        For ``ontologyOrValue`` fields, ``counts`` holds ontology value counts and
        ``other_counts`` holds free-text value counts.
        ``scope`` and ``qualifiers`` optionally restrict what is counted.
        Raises ValueError if field_id is unknown.
        """
        pass


@dataclass
class BeaconQueryResult(Generic[S]):
    """A query's total match count, and its records for record granularity."""

    total: int
    result_sets: BeaconResultSets[S]


class BeaconQueryService(ABC, Generic[S]):
    """Beacon V2 query endpoint."""

    @abstractmethod
    async def query(
        self,
        filters: list[BeaconQueryFilter],
        granularity: BeaconQueryGranularity = "record",
        scope: str | None = None,
        qualifiers: Mapping[str, Sequence[str]] | None = None,
    ) -> BeaconQueryResult[S]:
        pass
