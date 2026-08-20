from typing import Any, override

from search_api.exceptions import SystemException
from search_api.api.beacon.models import BeaconResultSet, BeaconResultSets
from search_api.api.bigpicture.models import (
    BigpictureBeaconDatasetResult,
    BigpictureBeaconImageResult,
)
from search_api.api.opensearch.beacon import OpenSearchQueryBeaconService
from search_api.api.opensearch.search import (
    count_documents,
    iter_paged_buckets,
    iter_paged_documents,
    top_hits_source,
    top_hits_sub_agg,
)

# How many results one round trip fetches.
_PAGE_SIZE = 1000

_DATASET_ID_FIELD = "dataset_id"
_IMAGE_ID_FIELD = "image_id"
_DATASET_OTHER_FIELDS = ("dataset_title", "dataset_description", "dataset_image_cnt")


class BigpictureDatasetBeaconService(
    OpenSearchQueryBeaconService[BigpictureBeaconDatasetResult]
):
    """OpenSearch beacon service grouping Bigpicture images into datasets."""

    # Get dataset metadata from one document per composite bucket.
    _SUB_AGGS = {"dataset_info": top_hits_sub_agg(list(_DATASET_OTHER_FIELDS))}

    @staticmethod
    def _build_dataset(
        dataset_id: str, bucket: dict[str, Any]
    ) -> BigpictureBeaconDatasetResult:
        source = top_hits_source(bucket, "dataset_info")
        for f in _DATASET_OTHER_FIELDS:
            if f not in source:
                raise SystemException(f"Dataset '{dataset_id}' is missing field: {f}")
        return BigpictureBeaconDatasetResult(
            datasetId=dataset_id,
            datasetTitle=source["dataset_title"],
            datasetDescription=source["dataset_description"],
            datasetUrl=f"https://datasets.bigpicture.eu/datasets/{dataset_id.lower()}.html",
            totalImageCount=source["dataset_image_cnt"],
            matchingImageCount=0,
            imageIds=[],
        )

    @override
    async def _get_count(self, query_clause: dict[str, Any]) -> int:
        # Datasets can be counted by grouping by dataset_id without sub aggregations.
        dataset_ids = {
            bucket["key"][_DATASET_ID_FIELD]
            async for bucket in iter_paged_buckets(
                self.client,
                self.index_name,
                query_clause,
                _PAGE_SIZE,
                # One composite-aggregation source. Documents are grouped to buckets
                # by their dataset_id value. The outer key names the source.
                sources=[{_DATASET_ID_FIELD: {"terms": {"field": _DATASET_ID_FIELD}}}],
            )
        }
        return len(dataset_ids)

    @override
    async def _get_records(
        self, query_clause: dict[str, Any]
    ) -> BeaconResultSets[BigpictureBeaconDatasetResult]:
        # image_id as a second composite source makes each bucket one image
        # within a dataset, rather than the whole dataset.
        datasets: dict[str, BigpictureBeaconDatasetResult] = {}
        async for bucket in iter_paged_buckets(
            self.client,
            self.index_name,
            query_clause,
            _PAGE_SIZE,
            # Two composite-aggregation sources. Documents are grouped to buckets
            # by their dataset_id and image_id values together. Each bucket is one
            # image within a dataset, not the whole dataset. This is required to
            # report which images matched, since the list of image ids is included
            # in the result.
            sources=[
                {_DATASET_ID_FIELD: {"terms": {"field": _DATASET_ID_FIELD}}},
                {_IMAGE_ID_FIELD: {"terms": {"field": _IMAGE_ID_FIELD}}},
            ],
            # Fetches dataset title, description, and image count from one
            # representative document per bucket. The composite key alone
            # only has dataset_id and image_id, not these fields.
            sub_aggs=BigpictureDatasetBeaconService._SUB_AGGS,
        ):
            dataset_id = bucket["key"][_DATASET_ID_FIELD]
            if dataset_id not in datasets:
                datasets[dataset_id] = BigpictureDatasetBeaconService._build_dataset(
                    dataset_id, bucket
                )
            datasets[dataset_id].imageIds.append(bucket["key"][_IMAGE_ID_FIELD])
            datasets[dataset_id].matchingImageCount += 1

        results: BeaconResultSets[BigpictureBeaconDatasetResult] = BeaconResultSets()
        for dataset_id, result in datasets.items():
            results.resultSet.append(
                BeaconResultSet[BigpictureBeaconDatasetResult](
                    id=dataset_id, results=[result]
                )
            )
        return results


class BigpictureImageBeaconService(
    OpenSearchQueryBeaconService[BigpictureBeaconImageResult]
):
    """OpenSearch beacon service serving individual Bigpicture images."""

    @override
    async def _get_count(self, query_clause: dict[str, Any]) -> int:
        return await count_documents(self.client, self.index_name, query_clause)

    @override
    async def _get_records(
        self, query_clause: dict[str, Any]
    ) -> BeaconResultSets[BigpictureBeaconImageResult]:
        records = [
            BeaconResultSet[BigpictureBeaconImageResult](
                id=source[_IMAGE_ID_FIELD],
                setType="image",
                results=[BigpictureBeaconImageResult(imageId=source[_IMAGE_ID_FIELD])],
            )
            async for source in iter_paged_documents(
                self.client,
                self.index_name,
                query_clause,
                _PAGE_SIZE,
                source_fields=[_IMAGE_ID_FIELD],
                sort_field=_IMAGE_ID_FIELD,
            )
        ]
        return BeaconResultSets(resultSet=records)
