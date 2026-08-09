import asyncio
from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from typing import Any, Generic, TypeVar, override

from opensearchpy import AsyncOpenSearch

from search_api.api.opensearch.services import (
    fetch_indexed_keywords,
    build_match_query,
    build_term_query,
    build_terms_query,
    build_iso8601_range_query,
    or_queries,
)
from search_api.api.qualifiers import QUALIFIERS_FIELD, encode_qualifier_value
from search_api.api.scopes import SCOPE_FIELD
from search_api.exceptions import SystemException, UserException
from search_api.services.ontology.service import get_ontology_service

from search_api.api.beacon.models import (
    BeaconFilteringQualifier,
    BeaconFilteringScope,
    BeaconFilteringTerm,
    BeaconQueryFilter,
    BeaconQueryGranularity,
    BeaconResultSet,
    BeaconResultSetResult,
    BeaconResultSets,
)
from search_api.api.models import ValueCounts, ValueCountsKey
from search_api.api.opensearch.models import (
    OpenSearchOntologyOrValue,
    OpenSearchBeaconFilteringTerm,
)

T = TypeVar("T", bound=BeaconFilteringTerm)
S = TypeVar("S", bound=BeaconResultSetResult)


def build_filtering_term_query(
    term: OpenSearchBeaconFilteringTerm, value: str | list[str]
) -> dict[str, Any]:
    """Return the clause matching one filtering term's requested value.

    The term's type selects the query: ``terms`` for the exact-match types, ``match``
    for ``text``, a day range for ``iso8601Range``. An ``ontologyOrValue`` term spans
    two fields, so concept ids and free text are each sent to their own. Several
    requested values are OR'd, so any one of them matches.

    The field is named by its full dotted path, which is what a nested query expects.
    The clause is a fragment: ``_nest_group_filters`` decides where it goes.
    """
    field = term.opensearch_field
    values = value if isinstance(value, list) else [value]

    if isinstance(field, OpenSearchOntologyOrValue):
        if term.ontology is None:
            raise SystemException(
                f"Filtering term '{term.id}' has no ontology configured."
            )
        ontology = get_ontology_service(term.ontology.id)
        # Search concept IDs and other values in their respective fields.
        concept_ids = [v for v in values if ontology.is_concept_id(v)]
        other_values = [v for v in values if not ontology.is_concept_id(v)]
        queries = []
        if concept_ids:
            queries.append(build_terms_query(field.concept_value_field, concept_ids))
        if other_values:
            queries.append(build_terms_query(field.other_value_field, other_values))
        return or_queries(queries)

    # field is str for all remaining term types.
    if term.type in ("controlledValue", "ontology", "keyword"):
        return or_queries([build_terms_query(field, values)])

    if term.type == "text":
        return or_queries([build_match_query(field, v) for v in values])

    if term.type == "iso8601Range":
        return or_queries([build_iso8601_range_query(field, v) for v in values])

    raise UserException(f"Unsupported term type {term.type}")


class BeaconService(ABC, Generic[T, S]):
    def __init__(self, filtering_terms: Sequence[T]) -> None:
        self.filtering_terms = filtering_terms

    def get_term(self, field_id: str) -> T:
        for term in self.filtering_terms:
            if term.id == field_id:
                return term
        raise UserException(f"Unknown field: '{field_id}'.")

    @abstractmethod
    async def query(
        self,
        filters: list[BeaconQueryFilter],
        granularity: BeaconQueryGranularity = "record",
        scope: str | None = None,
        qualifiers: Mapping[str, Sequence[str]] | None = None,
    ) -> BeaconResultSets[S]:
        pass

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
        filtering_scopes: Sequence[BeaconFilteringScope] = (),
        filtering_qualifiers: Sequence[BeaconFilteringQualifier] = (),
    ) -> None:
        super().__init__(filtering_terms)
        self.client = client
        self.index_name = index_name
        self.filtering_scopes = filtering_scopes
        self.filtering_qualifiers = filtering_qualifiers
        # Cached value counts.
        self._value_counts: dict[ValueCountsKey, ValueCounts] = {}

    def _qualifier_clauses_by_group(
        self, qualifiers: Mapping[str, Sequence[str]] | None
    ) -> dict[str, list[dict[str, Any]]]:
        """Return the qualifier filter clauses to apply, keyed by nested group.

        A qualifier filters nested groups by the qualifier id and value. A
        qualifier that is absent from ``qualifiers`` is not filtered on.
        Clauses are only produced for the groups that use the particular
        qualifier.
        """
        clauses: dict[str, list[dict[str, Any]]] = {}
        for qualifier in self.filtering_qualifiers:
            values = (qualifiers or {}).get(qualifier.id)
            if not values:
                continue
            for group in qualifier.groups:
                clauses.setdefault(group, []).append(
                    build_terms_query(
                        f"{group}.{QUALIFIERS_FIELD}",
                        [encode_qualifier_value(qualifier.id, v) for v in values],
                    )
                )
        return clauses

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

    def clear_value_counts(self) -> None:
        """Clear field's cached value counts."""
        self._value_counts.clear()

    async def refresh_value_counts(self, key: ValueCountsKey) -> None:
        """Refresh field's cached value counts."""
        self._value_counts[key] = await self._count_values(key)

    @override
    async def get_value_counts(
        self,
        field_id: str,
        scope: str | None = None,
        qualifiers: Mapping[str, Sequence[str]] | None = None,
    ) -> ValueCounts:
        """Return field's value counts.

        Value counts are returned from the cache. If they are not
        in the cache, they are retrieved from OpenSearch and added
        to the cache.
        """
        key = ValueCountsKey.of(field_id, scope, qualifiers)
        counts = self._value_counts.get(key)
        if counts is None:
            counts = await self._count_values(key)
            self._value_counts[key] = counts
        return counts

    async def _count_values(self, key: ValueCountsKey) -> ValueCounts:
        """Retrieve fields's value counts from OpenSearch."""
        term = self.get_term(key.field_id)
        field = term.opensearch_field
        document_filter = (
            build_term_query(SCOPE_FIELD, key.scope) if key.scope is not None else None
        )
        group_clauses = self._qualifier_clauses_by_group(
            key.qualifier_values_by_id
        ).get(term.group or "")
        group_item_filter = (
            {"bool": {"filter": group_clauses}} if group_clauses else None
        )
        if isinstance(field, OpenSearchOntologyOrValue):
            concept_counts, other_counts = await asyncio.gather(
                fetch_indexed_keywords(
                    self.client,
                    self.index_name,
                    field.concept_value_field,
                    document_filter=document_filter,
                    group_item_filter=group_item_filter,
                ),
                fetch_indexed_keywords(
                    self.client,
                    self.index_name,
                    field.other_value_field,
                    document_filter=document_filter,
                    group_item_filter=group_item_filter,
                ),
            )
            return ValueCounts(counts=concept_counts, other_counts=other_counts)
        return ValueCounts(
            counts=await fetch_indexed_keywords(
                self.client,
                self.index_name,
                field,
                document_filter=document_filter,
                group_item_filter=group_item_filter,
            )
        )

    @staticmethod
    def _nest_group_filters(
        term_queries: Sequence[tuple[OpenSearchBeaconFilteringTerm, dict[str, Any]]],
        qualifier_clauses_by_group: Mapping[str, list[dict[str, Any]]],
    ) -> list[dict[str, Any]]:
        """Return the bool clauses matching the given filters.

        A filter on a top-level field is a clause on its own. Filters on fields
        in a group are collected into one nested query per group, so that a single
        group item has to satisfy all of them. That group's qualifier clauses
        join them inside the same nested query, so the qualifier holds for the
        item that matched.

        Does not know about scopes.

        Each query comes from ``term_queries`` unchanged; only its placement is
        decided here. For example, filtering ``dataset_title`` plus ``finding`` and
        ``finding_severity`` (both in ``finding``) for Bigpicture, with the
        ``observation`` qualifier requested, returns::

            [<dataset_title term query>,
             {"nested": {"path": "finding",
                         "query": {"bool": {"filter": [
                             <finding term query>,
                             <finding_severity term query>,
                             {"terms": {"finding.qualifiers":
                                        ["observation:confirmed"]}}]}}}}]

        Args:
            term_queries: Each filtering term with the query built for its value.
            qualifier_clauses_by_group: The qualifier clauses to apply, keyed by group.

        Returns:
            The clauses, in no particular order.
        """
        clauses: list[dict[str, Any]] = []
        filters_by_group: dict[str, list[dict[str, Any]]] = {}
        for term, query in term_queries:
            group = OpenSearchBeaconService._nested_path(term.opensearch_field)
            if group is None:
                clauses.append(query)
            else:
                filters_by_group.setdefault(group, []).append(query)

        for group, group_filters in filters_by_group.items():
            clauses.append(
                {
                    "nested": {
                        "path": group,
                        "query": {
                            "bool": {
                                "filter": [
                                    *group_filters,
                                    *qualifier_clauses_by_group.get(group, []),
                                ]
                            }
                        },
                    }
                }
            )
        return clauses

    def _get_query(
        self,
        filters: list[BeaconQueryFilter],
        scope: str | None = None,
        qualifiers: Mapping[str, Sequence[str]] | None = None,
    ) -> dict[str, Any]:
        """Build an OpenSearch query from Beacon filters.

        A field only constrains the scopes it is indexed for, because a filter on the
        wrong scope would exclude every document in it.

        The query is therefore one ``should`` branch per scope, each with the
        filters on fields indexed for it. For example, Bigpicture's ``diagnosis``
        filter is restricted to the ``clinical`` scope.

        When every filter applies to every scope, ``should`` branches collapse
        into one query instead.

        Every clause is a ``filter`` because the query is not ranked. ``_score`` is
        never read. This makes the filter context cheaper and cacheable.
        """
        terms_by_id = {t.id: t for t in self.filtering_terms}
        term_queries: list[tuple[OpenSearchBeaconFilteringTerm, dict[str, Any]]] = []
        for f in filters:
            if f.id not in terms_by_id:
                raise UserException(f"Unknown field: '{f.id}'.")
            term = terms_by_id[f.id]
            term_queries.append((term, build_filtering_term_query(term, f.value)))

        qualifier_clauses_by_group = self._qualifier_clauses_by_group(qualifiers)
        scopes = [scope] if scope is not None else [s.id for s in self.filtering_scopes]

        # No scopes or every filter applies to all of them.
        if not scopes or all(
            set(scopes) <= set(term.scopes) for term, _ in term_queries
        ):
            return {
                "bool": {
                    "filter": [
                        # The requested scope, if one was asked for.
                        *([build_term_query(SCOPE_FIELD, scope)] if scope else []),
                        # The field value filters.
                        *self._nest_group_filters(
                            term_queries, qualifier_clauses_by_group
                        ),
                    ]
                    # With neither, match every document.
                    or [{"match_all": {}}]
                }
            }

        return {
            "bool": {
                # One alternative per scope.
                "should": [
                    {
                        "bool": {
                            "filter": [
                                # The requested scope.
                                build_term_query(SCOPE_FIELD, s),
                                # The field value filters for the scope.
                                *self._nest_group_filters(
                                    [(t, q) for t, q in term_queries if s in t.scopes],
                                    qualifier_clauses_by_group,
                                ),
                            ]
                        }
                    }
                    for s in scopes
                ],
                # Matching one alternative is enough.
                "minimum_should_match": 1,
            }
        }

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

    def get_boolean_query(
        self,
        filters: list[BeaconQueryFilter],
        scope: str | None = None,
        qualifiers: Mapping[str, Sequence[str]] | None = None,
    ) -> dict[str, Any]:
        """Build a query for boolean granularity."""
        return {
            "size": 0,
            "query": self._get_query(filters, scope, qualifiers),
        }

    @override
    async def query(
        self,
        filters: list[BeaconQueryFilter],
        granularity: BeaconQueryGranularity = "record",
        scope: str | None = None,
        qualifiers: Mapping[str, Sequence[str]] | None = None,
    ) -> BeaconResultSets[S]:
        """Execute a query. Boolean query granularity is handled here. Count
        and record granularity is delegated to _get_result."""

        if granularity == "boolean":
            resp = await self.client.search(
                index=self.index_name,
                body=self.get_boolean_query(filters, scope, qualifiers),
            )
            return OpenSearchBeaconService._get_boolean_result(resp)

        query_clause = self._get_query(filters, scope, qualifiers)
        return await self._get_result(query_clause, granularity)
