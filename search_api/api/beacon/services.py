import asyncio
from abc import ABC, abstractmethod
from collections.abc import Sequence
from typing import Any, Generic, TypeVar, override

from opensearchpy import AsyncOpenSearch

from search_api.api.opensearch.services import (
    fetch_indexed_keywords,
    build_match_query,
    build_terms_query,
    build_iso8601_range_query,
    or_queries,
)
from search_api.services.snomed import is_concept_id

from search_api.api.beacon.models import (
    BeaconFilteringTerm,
    BeaconQueryFilter,
    BeaconQueryGranularity,
    BeaconResultSet,
    BeaconResultSetResult,
    BeaconResultSets,
)
from search_api.api.models import IndexedFieldValueCounts
from search_api.api.opensearch.models import (
    OpenSearchOntologyOrValue,
    OpenSearchBeaconFilteringTerm,
)

T = TypeVar("T", bound=BeaconFilteringTerm)
S = TypeVar("S", bound=BeaconResultSetResult)


def build_opensearch_query(
    term: OpenSearchBeaconFilteringTerm, value: str | list[str]
) -> dict[str, Any]:
    field = term.opensearch_field
    values = value if isinstance(value, list) else [value]

    if isinstance(field, OpenSearchOntologyOrValue):
        # Search concept IDs and other values in their respective fields.
        concept_ids = [v for v in values if is_concept_id(v)]
        other_values = [v for v in values if not is_concept_id(v)]
        queries = []
        if concept_ids:
            queries.append(build_terms_query(field.concept_value_field, concept_ids))
        if other_values:
            queries.append(build_terms_query(field.other_value_field, other_values))
        return or_queries(queries)

    # field is str for all remaining term types.
    if term.type in ("controlledValue", "ontology"):
        return or_queries([build_terms_query(field, values)])

    if term.type == "text":
        return or_queries([build_match_query(field, v) for v in values])

    if term.type == "iso8601Range":
        return or_queries([build_iso8601_range_query(field, v) for v in values])

    raise ValueError(f"Unsupported term type {term.type}")


class BeaconService(ABC, Generic[T, S]):
    def __init__(self, filtering_terms: Sequence[T]) -> None:
        self.filtering_terms = filtering_terms

    def get_term(self, field_id: str) -> T:
        for term in self.filtering_terms:
            if term.id == field_id:
                return term
        raise ValueError(f"Unsupported field: {field_id}")

    @abstractmethod
    async def query(
        self,
        filters: list[BeaconQueryFilter],
        granularity: BeaconQueryGranularity = "record",
    ) -> BeaconResultSets[S]:
        pass

    @abstractmethod
    async def is_healthy(self) -> bool:
        pass

    @abstractmethod
    async def get_indexed_field_value_counts(
        self, field_id: str
    ) -> IndexedFieldValueCounts:
        """Return value counts for the indexed fields mapped to field_id.

        For simple fields, only ``counts`` is populated.
        For ``ontologyOrValue`` fields, ``counts`` holds ontology value counts and
        ``other_counts`` holds free-text value counts.
        Raises ValueError if field_id is unknown.
        """
        pass


class OpenSearchBeaconService(BeaconService[OpenSearchBeaconFilteringTerm, S]):
    """Generic OpenSearch-backed Beacon V2 service.

    Handles query construction, boolean granularity, field value counts, and
    cluster health. Subclasses implement _get_result to define how count
    and record queries are made.
    """

    def __init__(
        self,
        client: AsyncOpenSearch,
        index_name: str,
        filtering_terms: Sequence[OpenSearchBeaconFilteringTerm],
    ) -> None:
        super().__init__(filtering_terms)
        self.client = client
        self.index_name = index_name

    @staticmethod
    def _nested_path(field: str | OpenSearchOntologyOrValue) -> str | None:
        """Return the OpenSearch nested path for a field, or None for top-level fields."""
        field_name = (
            field.concept_value_field
            if isinstance(field, OpenSearchOntologyOrValue)
            else field
        )
        prefix, _, rest = field_name.partition(".")
        return prefix if rest else None

    @override
    async def is_healthy(self) -> bool:
        """Return True if the OpenSearch cluster status is green or yellow."""
        try:
            resp = await self.client.cluster.health()
            return resp.get("status") in {"green", "yellow"}
        except Exception:
            return False

    @override
    async def get_indexed_field_value_counts(
        self, field_id: str
    ) -> IndexedFieldValueCounts:
        field = self.get_term(field_id).opensearch_field
        if isinstance(field, OpenSearchOntologyOrValue):
            concept_counts, other_counts = await asyncio.gather(
                fetch_indexed_keywords(
                    self.client, self.index_name, field.concept_value_field
                ),
                fetch_indexed_keywords(
                    self.client, self.index_name, field.other_value_field
                ),
            )
            return IndexedFieldValueCounts(
                counts=concept_counts, other_counts=other_counts
            )
        return IndexedFieldValueCounts(
            counts=await fetch_indexed_keywords(self.client, self.index_name, field)
        )

    @staticmethod
    def _get_query(
        filters: list[BeaconQueryFilter],
        filtering_terms: Sequence[OpenSearchBeaconFilteringTerm],
    ) -> dict[str, Any]:
        """Build an OpenSearch bool/must query from Beacon filters.

        Filters on nested fields are grouped by path and wrapped in nested
        queries. Top-level filters are added as direct must clauses.
        """
        terms_by_id = {t.id: t for t in filtering_terms}
        must_clauses: list[dict[str, Any]] = []
        nested_filters: dict[str, list[dict[str, Any]]] = {}

        for f in filters:
            if f.id not in terms_by_id:
                raise ValueError(f"Unsupported field: {f.id}")
            term = terms_by_id[f.id]
            path = OpenSearchBeaconService._nested_path(term.opensearch_field)
            q = build_opensearch_query(term, f.value)
            if path is None:
                must_clauses.append(q)
            else:
                nested_filters.setdefault(path, []).append(q)

        for path, path_filters in nested_filters.items():
            must_clauses.append(
                {
                    "nested": {
                        "path": path,
                        "query": {"bool": {"filter": path_filters}},
                    }
                }
            )

        return {"bool": {"must": must_clauses or [{"match_all": {}}]}}

    @abstractmethod
    async def _get_result(
        self,
        query_clause: dict[str, Any],
        granularity: BeaconQueryGranularity,
    ) -> BeaconResultSets[S]:
        """Return results for the given query and count or record granularity."""
        pass

    @staticmethod
    def _get_boolean_result(resp: dict[str, Any]) -> BeaconResultSets[Any]:
        """Parse result for boolean query granularity."""
        results: BeaconResultSets[Any] = BeaconResultSets()
        if resp.get("hits", {}).get("total", {}).get("value", 0) > 0:
            results.resultSet.append(BeaconResultSet(id="", results=[]))
        return results

    def get_boolean_query(self, filters: list[BeaconQueryFilter]) -> dict[str, Any]:
        """Build a query for boolean granularity."""
        return {
            "size": 0,
            "query": self._get_query(filters, self.filtering_terms),
        }

    @override
    async def query(
        self,
        filters: list[BeaconQueryFilter],
        granularity: BeaconQueryGranularity = "record",
    ) -> BeaconResultSets[S]:
        """Execute a query. Boolean query granularity is handled here. Count
        and record granularity is delegated to _get_result."""
        query_clause = self._get_query(filters, self.filtering_terms)

        if granularity == "boolean":
            resp = await self.client.search(
                index=self.index_name, body=self.get_boolean_query(filters)
            )
            return OpenSearchBeaconService._get_boolean_result(resp)

        return await self._get_result(query_clause, granularity)
