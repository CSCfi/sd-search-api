from abc import ABC, abstractmethod
from typing import Any, override

from opensearchpy import AsyncOpenSearch

from search_api.services.search import (
    fetch_indexed_keywords,
    build_match_query,
    build_term_query,
    build_range_query,
    or_queries,
)

from search_api.api.beacon.models import (
    BeaconQueryFilter,
    BeaconResultSets,
    BeaconResultSet,
    BeaconResultSetResult,
    BeaconFilteringTerm,
)
from search_api.api.bigpicture.models import (
    BP_FILTERING_TERMS,
    BP_DATASET_SCOPE,
    BP_BIOLOGICAL_BEING_SCOPE,
    BP_SPECIMEN_SCOPE,
    BP_BLOCK_SCOPE,
    BP_STAINING_SCOPE,
)

BP_OPENSEARCH_INDEX = "bp-image-index"

# Map filter term to OpenSearch field.
BP_OPENSEARCH_FIELD: dict[str, str | list[str]] = {
    "dataset_title": "dataset_title",
    "dataset_description": "dataset_description",
    "animal_species": "species",
    "sex": "sex",
    "anatomical_site": "anatomical_site",
    "fixation_type": "fixation_type",
    "specimen_type": "specimen_type",
    "age_at_extraction": "age_at_extraction",
    "block_preparation": "block_preparation",
    "staining_target": "staining_target",
    "staining_procedure": ["staining_procedure", "staining_procedure_text"],
    "staining_compound": ["staining_compound", "staining_compound_text"],
}

BP_OPENSEARCH_FIELD_PATHS: dict[str, str] = {
    "animal_species": "blocks.species",
    "anatomical_site": "blocks.anatomical_site",
    "fixation_type": "blocks.fixation_type",
    "specimen_type": "blocks.specimen_type",
    "block_preparation": "blocks.block_preparation",
    "staining_procedure": "stains.staining_procedure",
    "staining_compound": "stains.staining_compound",
}

# TODO (improve): paginate to avoid limits
DEFAULT_LIMIT = 10000


def get_term(field_id: str) -> BeaconFilteringTerm:
    for term in BP_FILTERING_TERMS:
        if term.id == field_id:
            return term

    raise ValueError(f"Unsupported field: {field_id}")


def build_opensearch_query(term: BeaconFilteringTerm, value: str) -> dict[str, Any]:
    field_ids = BP_OPENSEARCH_FIELD[term.id]
    if isinstance(field_ids, str):
        field_ids = [field_ids]

    builders = {
        "text": build_match_query,
        "controlledVocabulary": build_term_query,
        "ontology": build_term_query,
        "ontologyOrValue": build_term_query,
        "numberRange": build_range_query,
    }

    builder = builders.get(term.type)
    if not builder:
        raise ValueError(f"Unsupported term type {term.type}")

    return or_queries([builder(f, value) for f in field_ids])


class BigpictureBeaconService(ABC):
    """
    Abstract Bigpicture Beacon search.
    """

    @abstractmethod
    async def query(
        self,
        filters: list[BeaconQueryFilter],
        limit: int = DEFAULT_LIMIT,
    ) -> BeaconResultSets:
        pass

    @abstractmethod
    async def is_healthy(self) -> bool:
        pass

    @abstractmethod
    async def get_indexed_values(self, field_id: str) -> set[str] | None:
        """Return indexed values or None if unsupported."""
        pass

    @abstractmethod
    async def get_indexed_value_counts(self, field_id: str) -> dict[str, int] | None:
        """Return indexed values with counts, or None if unsupported."""
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


class MockBigpictureBeaconService(BigpictureBeaconService):
    """
    Mock Bigpicture Beacon search.
    """

    @override
    async def query(
        self,
        filters: list[BeaconQueryFilter],
        limit: int = DEFAULT_LIMIT,
    ) -> BeaconResultSets:
        return get_mock_query_result()

    @override
    async def is_healthy(self) -> bool:
        return True

    @override
    async def get_indexed_values(self, field_id: str) -> set[str] | None:
        return None

    @override
    async def get_indexed_value_counts(self, field_id: str) -> dict[str, int] | None:
        return None


class OpenSearchBigpictureBeaconService(BigpictureBeaconService):
    """
    OpenSearch Bigpicture Beacon search.
    """

    def __init__(self, host: str, port: int, user: str, password: str):
        self.client = self._create_client(host, port, user, password)
        self.index_name = BP_OPENSEARCH_INDEX

    @staticmethod
    def _create_client(
        host: str, port: int, user: str, password: str
    ) -> AsyncOpenSearch:
        return AsyncOpenSearch(
            hosts=[{"host": host, "port": port}],
            http_auth=(user, password),
            use_ssl=True,
            verify_certs=False,
        )

    @override
    async def is_healthy(self) -> bool:
        try:
            resp = await self.client.cluster.health()
            return resp.get("status") in {"green", "yellow"}
        except Exception:
            return False

    @override
    async def get_indexed_values(self, field_id: str) -> set[str] | None:
        counts = await self.get_indexed_value_counts(field_id)
        return set(counts.keys()) if counts is not None else None

    @override
    async def get_indexed_value_counts(self, field_id: str) -> dict[str, int] | None:
        field_path = BP_OPENSEARCH_FIELD_PATHS.get(field_id)
        if field_path is None:
            return None
        return await fetch_indexed_keywords(self.index_name, field_path)

    @staticmethod
    def get_query(
        filters: list[BeaconQueryFilter], limit: int = DEFAULT_LIMIT
    ) -> dict[str, Any]:

        block_filters = []
        stain_filters = []
        must_clauses = []

        for f in filters:
            term = get_term(f.id)
            value = f.value

            if term.scopes == BP_DATASET_SCOPE:
                must_clauses.append(build_opensearch_query(term, value))
            elif term.scopes in (
                BP_BIOLOGICAL_BEING_SCOPE,
                BP_SPECIMEN_SCOPE,
                BP_BLOCK_SCOPE,
            ):
                block_filters.append(build_opensearch_query(term, value))
            elif term.scopes in (BP_STAINING_SCOPE,):
                stain_filters.append(build_opensearch_query(term, value))

        if block_filters:
            must_clauses.append(
                {
                    "nested": {
                        "path": "blocks",
                        "query": {"bool": {"filter": block_filters}},
                    }
                }
            )

        if stain_filters:
            must_clauses.append(
                {
                    "nested": {
                        "path": "stains",
                        "query": {"bool": {"filter": stain_filters}},
                    }
                }
            )

        return {
            "size": 0,
            "query": {"bool": {"must": must_clauses or [{"match_all": {}}]}},
            "aggs": {
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
                        "image_result": {"terms": {"field": "image_id", "size": limit}},
                    },
                }
            },
        }

    @override
    async def query(
        self,
        filters: list[BeaconQueryFilter],
        limit: int = DEFAULT_LIMIT,
    ) -> BeaconResultSets:
        _query = self.get_query(filters, limit)
        resp = await self.client.search(index=self.index_name, body=_query)
        buckets = resp.get("aggregations", {}).get("datasets", {}).get("buckets", [])

        results = BeaconResultSets()

        for bucket in buckets:
            dataset_id = bucket["key"]
            matching_image_count = bucket["doc_count"]

            hits = bucket["dataset_result"]["hits"]["hits"]
            hit_source = hits[0]["_source"] if hits else {}

            dataset_title = hit_source.get("dataset_title")
            dataset_description = hit_source.get("dataset_description")
            total_image_count = hit_source.get("dataset_image_cnt", 0)
            image_ids = [b["key"] for b in bucket["image_result"]["buckets"]]

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
