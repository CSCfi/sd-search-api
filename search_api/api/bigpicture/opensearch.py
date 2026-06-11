from typing import Any, override

from search_api.api.beacon.models import (
    BeaconQueryGranularity,
    BeaconResultSet,
    BeaconResultSetResult,
    BeaconResultSets,
)
from search_api.api.beacon.services import OpenSearchBeaconService

_COMPOSITE_PAGE_SIZE = 1000


class BigpictureOpenSearchBeaconService(OpenSearchBeaconService):
    """OpenSearch beacon service with Bigpicture document-specific query logic."""

    @staticmethod
    def _build_composite_body(
        query: dict[str, Any],
        include_image_ids: bool,
        after_key: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Build an OpenSearch composite aggregation body for one page."""
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
    ) -> BeaconResultSets:
        """Return results for the given query and count or record granularity."""
        include_image_ids = granularity == "record"
        result_sets: dict[str, BeaconResultSetResult] = {}
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
                            raise ValueError(
                                f"Dataset '{dataset_id}' is missing field: {f}"
                            )
                    dataset_title = source["dataset_title"]
                    dataset_description = source["dataset_description"]
                    dataset_image_cnt = source["dataset_image_cnt"]
                    result = BeaconResultSetResult(
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

        results = BeaconResultSets()
        for dataset_id, result in result_sets.items():
            results.resultSet.append(BeaconResultSet(id=dataset_id, results=[result]))
        return results
