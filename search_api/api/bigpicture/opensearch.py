from typing import Any, override

from search_api.exceptions import SystemException
from search_api.api.beacon.models import (
    BeaconQueryGranularity,
    BeaconResultSet,
    BeaconResultSets,
)
from search_api.api.beacon.services import OpenSearchBeaconService
from search_api.api.bigpicture.models import BigpictureBeaconResultSetResult

_COMPOSITE_PAGE_SIZE = 1000


class BigpictureOpenSearchBeaconService(
    OpenSearchBeaconService[BigpictureBeaconResultSetResult]
):
    """OpenSearch beacon service for the Bigpicture document schema.

    Bigpicture documents are indexed one per image, with dataset-level fields
    (dataset_id, dataset_title, dataset_description, dataset_image_cnt) stored
    on every document, and nested types (blocks, stains) for other data.
    Results are grouped by dataset_id using composite aggregation.
    """

    @staticmethod
    def _build_composite_body(
        query: dict[str, Any],
        include_image_ids: bool,
        after_key: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Build an OpenSearch composite aggregation request body for one page.

        Composite sources are always keyed by dataset_id; image_id is added as
        a second source for record granularity so each bucket represents a single
        image. A top_hits sub-aggregation fetches dataset metadata (title,
        description, total image count) from one document per bucket. Pass
        after_key from the previous response to advance to the next page.
        """
        sources: list[dict[str, Any]] = [
            {"dataset_id": {"terms": {"field": "dataset_id"}}}
        ]
        if include_image_ids:
            sources.append({"image_id": {"terms": {"field": "image_id"}}})

        composite: dict[str, Any] = {"size": _COMPOSITE_PAGE_SIZE, "sources": sources}
        if after_key:
            composite["after"] = after_key

        return {
            "size": 0,
            "query": query,
            "aggs": {
                "pages": {
                    "composite": composite,
                    "aggs": {
                        "dataset_info": {
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
    async def _get_result(
        self,
        query_clause: dict[str, Any],
        granularity: BeaconQueryGranularity,
    ) -> BeaconResultSets[BigpictureBeaconResultSetResult]:
        """Aggregate matching images by dataset using composite aggregation.

        Pages through all composite buckets via after_key cursors. For count
        granularity, buckets are keyed by dataset_id and doc_count accumulates
        the matching image count. For record granularity, image_id is added as
        a composite source so each bucket represents one image, and image IDs
        are collected into a list.
        """
        include_image_ids = granularity == "record"
        result_sets: dict[str, BigpictureBeaconResultSetResult] = {}
        after_key: dict[str, Any] | None = None

        while True:
            body = BigpictureOpenSearchBeaconService._build_composite_body(
                query_clause, include_image_ids, after_key
            )
            resp = await self.client.search(index=self.index_name, body=body)
            agg = resp["aggregations"]["pages"]

            for bucket in agg["buckets"]:
                dataset_id = bucket["key"]["dataset_id"]

                if dataset_id not in result_sets:
                    hits = bucket["dataset_info"]["hits"]["hits"]
                    source = hits[0]["_source"] if hits else {}
                    for f in (
                        "dataset_title",
                        "dataset_description",
                        "dataset_image_cnt",
                    ):
                        if f not in source:
                            raise SystemException(
                                f"Dataset '{dataset_id}' is missing field: {f}"
                            )
                    dataset_title = source["dataset_title"]
                    dataset_description = source["dataset_description"]
                    dataset_image_cnt = source["dataset_image_cnt"]
                    result = BigpictureBeaconResultSetResult(
                        datasetId=dataset_id,
                        datasetTitle=dataset_title,
                        datasetDescription=dataset_description,
                        totalImageCount=dataset_image_cnt,
                        matchingImageCount=0,
                        imageIds=[],
                    )
                    result_sets[dataset_id] = result
                else:
                    result = result_sets[dataset_id]

                if include_image_ids:
                    result.imageIds.append(bucket["key"]["image_id"])
                    result.matchingImageCount += 1
                else:
                    result.matchingImageCount += bucket["doc_count"]

            after_key = agg.get("after_key")
            if not after_key:
                break

        results: BeaconResultSets[BigpictureBeaconResultSetResult] = BeaconResultSets()
        for dataset_id, result in result_sets.items():
            results.resultSet.append(
                BeaconResultSet[BigpictureBeaconResultSetResult](
                    id=dataset_id, results=[result]
                )
            )
        return results
