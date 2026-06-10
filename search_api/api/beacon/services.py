import asyncio
from abc import ABC, abstractmethod
from collections.abc import Sequence
from functools import partial
from typing import Any, override

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
    BeaconQueryFilter,
    BeaconQueryGranularity,
    BeaconResultSets,
    BeaconResultSet,
    BeaconResultSetResult,
)
from search_api.api.opensearch.models import (
    OpenSearchOntologyOrValue,
    OpenSearchBeaconFilteringTerm,
)

# TODO (improve): paginate to avoid limits
DEFAULT_LIMIT = 10000


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


class BeaconService(ABC):
    def __init__(
        self, filtering_terms: Sequence[OpenSearchBeaconFilteringTerm]
    ) -> None:
        self.filtering_terms = filtering_terms

    def get_term(self, field_id: str) -> OpenSearchBeaconFilteringTerm:
        for term in self.filtering_terms:
            if term.id == field_id:
                return term
        raise ValueError(f"Unsupported field: {field_id}")

    @abstractmethod
    async def query(
        self,
        filters: list[BeaconQueryFilter],
        granularity: BeaconQueryGranularity = "record",
        limit: int = DEFAULT_LIMIT,
    ) -> BeaconResultSets:
        pass

    @abstractmethod
    async def is_healthy(self) -> bool:
        pass

    @abstractmethod
    async def get_indexed_field_value_counts(
        self, field_id: str
    ) -> list[dict[str, int]]:
        """Return value counts for each OpenSearch field mapped to field_id.

        Returns one dict for simple fields and two dicts for ontologyOrValue fields
        (concept field first, other-value field second).
        Raises ValueError if field_id is unknown.
        """
        pass


def get_mock_query_result() -> BeaconResultSets:
    results = BeaconResultSets()
    results.resultSet.append(
        BeaconResultSet(
            id="testDataset",
            results=[
                BeaconResultSetResult(
                    datasetId="testDataset",
                    datasetTitle="testTitle",
                    datasetDescription="testDescription",
                    totalImageCount=1,
                    matchingImageCount=1,
                    imageIds=["testImage"],
                )
            ],
        )
    )
    return results


class MockBeaconService(BeaconService):
    @override
    async def query(
        self,
        filters: list[BeaconQueryFilter],
        granularity: BeaconQueryGranularity = "record",
        limit: int = DEFAULT_LIMIT,
    ) -> BeaconResultSets:
        return get_mock_query_result()

    @override
    async def is_healthy(self) -> bool:
        return True

    @override
    async def get_indexed_field_value_counts(
        self, field_id: str
    ) -> list[dict[str, int]]:
        term = self.get_term(field_id)
        if isinstance(term.opensearch_field, OpenSearchOntologyOrValue):
            return [{}, {}]
        return [{}]


class OpenSearchBeaconService(BeaconService):
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
        try:
            resp = await self.client.cluster.health()
            return resp.get("status") in {"green", "yellow"}
        except Exception:
            return False

    @override
    async def get_indexed_field_value_counts(
        self, field_id: str
    ) -> list[dict[str, int]]:
        field = self.get_term(field_id).opensearch_field
        if isinstance(field, OpenSearchOntologyOrValue):
            concept_field_counts, other_field_counts = await asyncio.gather(
                fetch_indexed_keywords(
                    self.client, self.index_name, field.concept_value_field
                ),
                fetch_indexed_keywords(
                    self.client, self.index_name, field.other_value_field
                ),
            )
            return [concept_field_counts, other_field_counts]
        return [await fetch_indexed_keywords(self.client, self.index_name, field)]

    @staticmethod
    def _get_query(
        filters: list[BeaconQueryFilter],
        filtering_terms: Sequence[OpenSearchBeaconFilteringTerm],
    ) -> dict[str, Any]:
        """Build the OpenSearch query clause from a list of Beacon filters."""
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

    @staticmethod
    def _get_count_and_record_aggs(
        limit: int, include_image_ids: bool
    ) -> dict[str, Any]:
        """Get datasets aggregation, optionally including image IDs."""
        return {
            "datasets": {
                "terms": {"field": "dataset_id", "size": limit},
                "aggs": {
                    "dataset_result": {
                        "top_hits": {
                            "size": 1,
                            "_source": [
                                "dataset_title",
                                "dataset_description",
                                "dataset_image_cnt",
                            ],
                        }
                    },
                    **(
                        {
                            "image_result": {
                                "terms": {"field": "image_id", "size": limit}
                            }
                        }
                        if include_image_ids
                        else {}
                    ),
                },
            }
        }

    @staticmethod
    def _parse_boolean_result(resp: dict[str, Any]) -> BeaconResultSets:
        """Parse a boolean query response into a BeaconResultSets."""
        results = BeaconResultSets()
        if resp.get("hits", {}).get("total", {}).get("value", 0) > 0:
            results.resultSet.append(BeaconResultSet(id="", results=[]))
        return results

    @staticmethod
    def _parse_count_and_record_result(
        resp: dict[str, Any], include_image_ids: bool
    ) -> BeaconResultSets:
        """Parse datasets aggregations result."""
        buckets = resp.get("aggregations", {}).get("datasets", {}).get("buckets", [])
        results = BeaconResultSets()
        for bucket in buckets:
            dataset_id = bucket["key"]
            matching_image_count = bucket["doc_count"]

            hits = bucket["dataset_result"]["hits"]["hits"]
            hit_source = hits[0]["_source"] if hits else {}

            dataset_title = hit_source.get("dataset_title")
            dataset_description = hit_source.get("dataset_description")
            total_image_count = hit_source.get("dataset_image_cnt")

            if dataset_title is None:
                raise ValueError(
                    f"Dataset '{dataset_id}' is missing field: dataset_title"
                )
            if dataset_description is None:
                raise ValueError(
                    f"Dataset '{dataset_id}' is missing field: dataset_description"
                )
            if total_image_count is None:
                raise ValueError(
                    f"Dataset '{dataset_id}' is missing field: dataset_image_cnt"
                )

            image_ids = (
                [b["key"] for b in bucket["image_result"]["buckets"]]
                if include_image_ids
                else []
            )

            results.resultSet.append(
                BeaconResultSet(
                    id=dataset_id,
                    results=[
                        BeaconResultSetResult(
                            datasetId=dataset_id,
                            datasetTitle=dataset_title,
                            datasetDescription=dataset_description,
                            totalImageCount=total_image_count,
                            matchingImageCount=matching_image_count,
                            imageIds=image_ids,
                        )
                    ],
                )
            )
        return results

    def get_boolean_query(self, filters: list[BeaconQueryFilter]) -> dict[str, Any]:
        return {
            "size": 0,
            "query": self._get_query(filters, self.filtering_terms),
        }

    def get_count_query(
        self, filters: list[BeaconQueryFilter], limit: int = DEFAULT_LIMIT
    ) -> dict[str, Any]:
        return {
            "size": 0,
            "query": self._get_query(filters, self.filtering_terms),
            "aggs": self._get_count_and_record_aggs(limit, include_image_ids=False),
        }

    def get_record_query(
        self, filters: list[BeaconQueryFilter], limit: int = DEFAULT_LIMIT
    ) -> dict[str, Any]:
        return {
            "size": 0,
            "query": self._get_query(filters, self.filtering_terms),
            "aggs": self._get_count_and_record_aggs(limit, include_image_ids=True),
        }

    @override
    async def query(
        self,
        filters: list[BeaconQueryFilter],
        granularity: BeaconQueryGranularity = "record",
        limit: int = DEFAULT_LIMIT,
    ) -> BeaconResultSets:
        if granularity == "boolean":
            body = self.get_boolean_query(filters)
            parse = OpenSearchBeaconService._parse_boolean_result
        elif granularity == "count":
            body = self.get_count_query(filters, limit)
            parse = partial(
                OpenSearchBeaconService._parse_count_and_record_result,
                include_image_ids=False,
            )
        else:  # granularity == "record"
            body = self.get_record_query(filters, limit)
            parse = partial(
                OpenSearchBeaconService._parse_count_and_record_result,
                include_image_ids=True,
            )

        resp = await self.client.search(index=self.index_name, body=body)
        return parse(resp)
