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
    get_documents,
    get_grouped_documents,
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

    @staticmethod
    def _accumulate_count(
        result: BigpictureBeaconDatasetResult, bucket: dict[str, Any]
    ) -> None:
        result.matchingImageCount += bucket["doc_count"]

    @staticmethod
    def _accumulate_image_id(
        result: BigpictureBeaconDatasetResult, bucket: dict[str, Any]
    ) -> None:
        result.imageIds.append(bucket["key"][_IMAGE_ID_FIELD])
        result.matchingImageCount += 1

    @override
    async def _get_count(self, query_clause: dict[str, Any]) -> int:
        datasets = await get_grouped_documents(
            search=self.client,
            index_name=self.index_name,
            query_clause=query_clause,
            page_size=_PAGE_SIZE,
            group_field=_DATASET_ID_FIELD,
            build_record=BigpictureDatasetBeaconService._build_dataset,
            accumulate_record=BigpictureDatasetBeaconService._accumulate_count,
            sub_aggs=BigpictureDatasetBeaconService._SUB_AGGS,
        )
        return len(datasets)

    @override
    async def _get_records(
        self, query_clause: dict[str, Any]
    ) -> BeaconResultSets[BigpictureBeaconDatasetResult]:
        datasets = await get_grouped_documents(
            search=self.client,
            index_name=self.index_name,
            query_clause=query_clause,
            page_size=_PAGE_SIZE,
            group_field=_DATASET_ID_FIELD,
            build_record=BigpictureDatasetBeaconService._build_dataset,
            accumulate_record=BigpictureDatasetBeaconService._accumulate_image_id,
            sub_aggs=BigpictureDatasetBeaconService._SUB_AGGS,
            extra_sources=[{_IMAGE_ID_FIELD: {"terms": {"field": _IMAGE_ID_FIELD}}}],
        )
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
        def build_record(
            source: dict[str, Any],
        ) -> BeaconResultSet[BigpictureBeaconImageResult]:
            image_id = source[_IMAGE_ID_FIELD]
            return BeaconResultSet[BigpictureBeaconImageResult](
                id=image_id,
                setType="image",
                results=[BigpictureBeaconImageResult(imageId=image_id)],
            )

        records = await get_documents(
            search=self.client,
            index_name=self.index_name,
            query_clause=query_clause,
            page_size=_PAGE_SIZE,
            source_fields=[_IMAGE_ID_FIELD],
            sort_field=_IMAGE_ID_FIELD,
            build_record=build_record,
        )
        return BeaconResultSets(resultSet=records)
