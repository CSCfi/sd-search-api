from abc import ABC, abstractmethod
from typing import Any, override

from opensearchpy import AsyncOpenSearch

from search_api.api.bigpicture.models import (
    BeaconQueryFilter,
    BeaconResultSets,
    BeaconResultSet,
    BeaconResultSetResult,
)

# TODO (improve): paginate to avoid limits

DEFAULT_LIMIT = 10000

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
    async def query(
        self,
        filters: list[BeaconQueryFilter],
        limit: int = DEFAULT_LIMIT,
    ) -> BeaconResultSets:
        """
        Get matching datasets.
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


class OpenSearchBigpictureBeaconService(BigpictureBeaconService):
    """
    OpenSearch Bigpicture Beacon search.
    """

    def __init__(self, client: AsyncOpenSearch, index_name: str):
        self.client = client
        self.index_name = index_name

    @staticmethod
    def get_query(
        filters: list[BeaconQueryFilter], limit: int = DEFAULT_LIMIT
    ) -> dict[str, Any]:

        block_filters = []
        stain_filters = []
        must_clauses = []

        for f in filters:
            field_id = f.id
            value = f.value

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
