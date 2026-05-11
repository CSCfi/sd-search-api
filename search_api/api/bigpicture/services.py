from abc import ABC, abstractmethod
from typing import Any, override

from opensearchpy import AsyncOpenSearch

DEFAULT_LIMIT = 1000

TEXT_FIELDS = {
    "dataset_title": "dataset_title",
    "dataset_description": "dataset_description",
}

BLOCK_TERM_FIELDS = {
    "species": "species",
    "sex": "sex",
    "anatomical_site": "anatomical_site",
    "fixation_type": "fixation_type",
    "specimen_type": "specimen_type",
    "block_preparation": "block_preparation",
}

BLOCK_RANGE_FIELDS = {"age_at_extraction": "age_at_extraction"}

STAIN_TERM_FIELDS = {
    "staining_method": "staining_method",
    "staining_target": "staining_target",
    "staining_procedure": "staining_procedure",
    "staining_compound": "staining_compound",
}


class BigpictureBeaconService(ABC):
    """
    Abstract Bigpicture Beacon search.
    """

    @abstractmethod
    async def query_datasets(
        self,
        filters: list[dict[str, Any]],
        limit: int,
        after_key: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Get matching datasets.
        """
        pass


def get_mock_results_sets() -> list[Any]:
    return [
        {
            "id": "testDataset",
            "resultsCount": 1,  # total matching image count
            "results": [
                {
                    "datasetId": "testDataset",
                    "datasetTitle": "testTitle",
                    "datasetDescription": "testDescription",
                    "totalImageCount": 1,
                    "matchingImageCount": 1,
                }
            ],
        }
    ]


class MockBigpictureBeaconService(BigpictureBeaconService):
    """
    Mock Bigpicture Beacon search.
    """

    @override
    async def query_datasets(
        self,
        filters: list[dict[str, Any]],
        limit: int,
        after_key: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {"result_sets": get_mock_results_sets()}


class OpenSearchBigpictureBeaconService(BigpictureBeaconService):
    """
    OpenSearch Bigpicture Beacon search.
    """

    def __init__(self, client: AsyncOpenSearch, index_name: str):
        self.client = client
        self.index_name = index_name

    @staticmethod
    def get_query(
        filters: list[dict[str, Any]],
        limit: int = DEFAULT_LIMIT,
        after_key: dict[str, Any] | None = None,
    ) -> dict[str, Any]:

        block_filters = []
        stain_filters = []
        must_clauses = []

        for f in filters:
            if "id" not in f:
                continue
            if "value" not in f:
                continue

            field_id = f["id"]
            value = f["value"]
            if field_id is None:
                continue
            if value is None:
                continue

            if field_id in TEXT_FIELDS:
                must_clauses.append({"match": {TEXT_FIELDS[field_id]: value}})

            elif field_id in BLOCK_TERM_FIELDS:
                block_filters.append({"term": {BLOCK_TERM_FIELDS[field_id]: value}})

            elif field_id in BLOCK_RANGE_FIELDS:
                r = {}
                parts = value.split("-", 1)

                if len(parts) > 0:
                    r["gte"] = r["lte"] = int(parts[0])

                if len(parts) > 1 and parts[1]:
                    r["lte"] = int(parts[1])

                block_filters.append({"range": {BLOCK_RANGE_FIELDS[field_id]: r}})

            elif field_id in STAIN_TERM_FIELDS:
                stain_filters.append({"term": {STAIN_TERM_FIELDS[field_id]: value}})

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
                    "composite": {
                        "size": limit,
                        "sources": [{"dataset_id": {"terms": {"field": "dataset_id"}}}],
                        **({"after": after_key} if after_key else {}),
                    },
                    "aggs": {
                        "dataset_metadata": {
                            "top_hits": {
                                "size": 1,
                                "_source": [
                                    "dataset_title",
                                    "dataset_description",
                                    "dataset_image_cnt",
                                ],
                            }
                        }
                    },
                }
            },
        }

    @override
    async def query_datasets(
        self,
        filters: list[dict[str, Any]],
        limit: int,
        after_key: dict[str, Any] | None = None,
    ) -> dict[str, Any]:

        _query = self.get_query(filters, limit, after_key)

        resp = await self.client.search(index=self.index_name, body=_query)

        buckets = resp.get("aggregations", {}).get("datasets", {}).get("buckets", [])

        result_sets = []

        for bucket in buckets:
            dataset_id = bucket["key"]
            matching_count = bucket["doc_count"]

            hits = bucket["dataset_metadata"]["hits"]["hits"]
            hit_source = hits[0]["_source"] if hits else {}

            dataset_title = hit_source.get("dataset_title")
            dataset_description = hit_source.get("dataset_description")
            total_image_count = hit_source.get("dataset_image_cnt")

            result_sets.append(
                {
                    "id": dataset_id,
                    "resultsCount": matching_count,
                    "results": [
                        {
                            "datasetId": dataset_id,
                            "datasetTitle": dataset_title,
                            "datasetDescription": dataset_description,
                            "totalImageCount": total_image_count,
                            "matchingImageCount": matching_count,
                        }
                    ],
                }
            )

        next_cursor = resp.get("aggregations", {}).get("datasets", {}).get("after_key")

        return {"result_sets": result_sets, "next_cursor": next_cursor}
