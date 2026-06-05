import asyncio
from abc import ABC, abstractmethod
from typing import Any, override

from opensearchpy import AsyncOpenSearch
from pydantic import BaseModel

from search_api.services.search import (
    fetch_indexed_keywords,
    build_match_query,
    build_terms_query,
    build_iso8601_range_query,
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


class OpenSearchOntologyOrValue(BaseModel):
    concept_value_field: str
    other_value_field: str


# Map filter term to OpenSearch field.
BP_OPENSEARCH_FIELD: dict[str, str | OpenSearchOntologyOrValue] = {
    "dataset_title": "dataset_title",
    "dataset_description": "dataset_description",
    "animal_species": "blocks.species",
    "sex": "blocks.sex",
    "anatomical_site": "blocks.anatomical_site",
    "fixation_type": OpenSearchOntologyOrValue(
        concept_value_field="blocks.fixation_type",
        other_value_field="blocks.fixation_type_text",
    ),
    "specimen_type": "blocks.specimen_type",
    "age_at_extraction": "blocks.age_at_extraction",
    "block_preparation": "blocks.block_preparation",
    "staining_target": "stains.staining_target",
    "staining_procedure": OpenSearchOntologyOrValue(
        concept_value_field="stains.staining_procedure",
        other_value_field="stains.staining_procedure_text",
    ),
    "staining_compound": OpenSearchOntologyOrValue(
        concept_value_field="stains.staining_compound",
        other_value_field="stains.staining_compound_text",
    ),
}

# TODO (improve): paginate to avoid limits
DEFAULT_LIMIT = 10000


def get_term(field_id: str) -> BeaconFilteringTerm:
    for term in BP_FILTERING_TERMS:
        if term.id == field_id:
            return term

    raise ValueError(f"Unsupported field: {field_id}")


def build_opensearch_query(
    term: BeaconFilteringTerm, value: str | list[str]
) -> dict[str, Any]:
    field = BP_OPENSEARCH_FIELD[term.id]
    if isinstance(field, OpenSearchOntologyOrValue):
        field_paths = [field.concept_value_field, field.other_value_field]
    else:
        field_paths = [field]

    values = value if isinstance(value, list) else [value]

    if term.type in ("controlledValue", "ontology", "ontologyOrValue"):
        # Use a single terms query per field for efficient multi-value exact matching.
        return or_queries([build_terms_query(f, values) for f in field_paths])

    if term.type == "text":
        return or_queries(
            [build_match_query(f, v) for f in field_paths for v in values]
        )

    if term.type == "iso8601Range":
        return or_queries(
            [build_iso8601_range_query(f, v) for f in field_paths for v in values]
        )

    raise ValueError(f"Unsupported term type {term.type}")


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
    async def get_indexed_field_value_counts(
        self, field_id: str
    ) -> list[dict[str, int]]:
        """Return value counts for each OpenSearch field mapped to field_id.

        Returns one dict per field in the order defined by BP_OPENSEARCH_FIELD.
        Raises ValueError if field_id is not in BP_OPENSEARCH_FIELD.
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
    async def get_indexed_field_value_counts(
        self, field_id: str
    ) -> list[dict[str, int]]:
        field = BP_OPENSEARCH_FIELD.get(field_id)
        if field is None:
            raise ValueError(f"Unknown field: '{field_id}'")
        if isinstance(field, OpenSearchOntologyOrValue):
            return [{}, {}]
        return [{}]


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
    async def get_indexed_field_value_counts(
        self, field_id: str
    ) -> list[dict[str, int]]:
        field = BP_OPENSEARCH_FIELD.get(field_id)
        if field is None:
            raise ValueError(f"Unknown field: '{field_id}'")
        if isinstance(field, OpenSearchOntologyOrValue):
            concept_field_counts, other_field_counts = await asyncio.gather(
                fetch_indexed_keywords(self.index_name, field.concept_value_field),
                fetch_indexed_keywords(self.index_name, field.other_value_field),
            )
            return [concept_field_counts, other_field_counts]
        return [await fetch_indexed_keywords(self.index_name, field)]

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
