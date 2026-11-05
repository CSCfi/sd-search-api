from typing import Any, ClassVar, override
from urllib.parse import quote

from search_api.api.beacon.models import BeaconResultSet, BeaconResultSets
from search_api.api.bigpicture.models import (
    BP_DATASET_ID_FIELD,
    BP_DOCUMENT_FIELDS,
    BigpictureBeaconDatasetResult,
    BigpictureBeaconImageResult,
)
from search_api.api.opensearch.beacon import OpenSearchQueryBeaconService
from search_api.api.opensearch.clauses import build_terms_clause
from search_api.api.opensearch.search import (
    count_documents,
    iter_paged_buckets,
    iter_paged_documents,
    take_page,
    top_hits_source,
    top_hits_sub_agg,
)
from search_api.exceptions import SystemException

# How many results one round trip fetches.
_FETCH_SIZE = 1000


def _fetch_size(offset: int, limit: int) -> int:
    """How many results to fetch per round trip for a page, at most _FETCH_SIZE.

    :param offset: The number of records skipped.
    :param limit: The most records in the page, or 0 for every record.
    """
    return min(_FETCH_SIZE, offset + limit) if limit else _FETCH_SIZE


_IMAGE_ID_FIELD = "image_id"
_DATASET_OTHER_FIELDS = ("dataset_title", "dataset_description", "dataset_image_cnt")

# Validate field constants against fields.yaml.
for _field_id in (BP_DATASET_ID_FIELD, _IMAGE_ID_FIELD, *_DATASET_OTHER_FIELDS):
    if _field_id not in BP_DOCUMENT_FIELDS:
        raise SystemException(f"'{_field_id}' is not a declared field in fields.yaml.")


def _group_by(field_id: str) -> dict[str, Any]:
    """One composite-aggregation source, grouping by a field's own value."""
    return {field_id: {"terms": {"field": field_id}}}


# Groups by dataset_id alone, so one bucket is one dataset.
_DATASET_COUNT_SOURCES = [_group_by(BP_DATASET_ID_FIELD)]

# Groups by dataset_id and image_id together, so one bucket is one image
# within a dataset. This is needed to report which images matched.
_DATASET_RECORD_SOURCES = [_group_by(BP_DATASET_ID_FIELD), _group_by(_IMAGE_ID_FIELD)]


class BigpictureDatasetBeaconService(
    OpenSearchQueryBeaconService[BigpictureBeaconDatasetResult]
):
    """OpenSearch beacon service grouping Bigpicture images into datasets."""

    # Get dataset metadata from one document per composite bucket.
    _SUB_AGGS: ClassVar[dict[str, Any]] = {
        "dataset_info": top_hits_sub_agg(list(_DATASET_OTHER_FIELDS))
    }

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
            datasetUrl=f"https://datasets.bigpicture.eu/datasets/{quote(dataset_id.lower(), safe='')}.html",
            totalImageCount=source["dataset_image_cnt"],
            matchingImageCount=0,
            imageIds=[],
        )

    @override
    async def _get_count(self, query_clause: dict[str, Any]) -> int:
        # Datasets can be counted by grouping by dataset_id without sub aggregations.
        dataset_ids = {
            bucket["key"][BP_DATASET_ID_FIELD]
            async for bucket in iter_paged_buckets(
                self.client,
                self.index_name,
                query_clause,
                _FETCH_SIZE,
                sources=_DATASET_COUNT_SOURCES,
            )
        }
        return len(dataset_ids)

    @override
    async def _get_records(
        self, query_clause: dict[str, Any], offset: int, limit: int
    ) -> BeaconResultSets[BigpictureBeaconDatasetResult]:
        # Pages count datasets, not images. First find the dataset ids on this
        # page, then read the matching images of those datasets only. When the
        # page is every dataset, the query selects them already.
        page_clause = query_clause
        if offset or limit:
            dataset_buckets = await take_page(
                iter_paged_buckets(
                    self.client,
                    self.index_name,
                    query_clause,
                    _fetch_size(offset, limit),
                    sources=_DATASET_COUNT_SOURCES,
                ),
                offset,
                limit,
            )
            dataset_ids = [
                bucket["key"][BP_DATASET_ID_FIELD] for bucket in dataset_buckets
            ]
            if not dataset_ids:
                return BeaconResultSets()
            page_clause = {
                "bool": {
                    "filter": [
                        query_clause,
                        build_terms_clause(BP_DATASET_ID_FIELD, dataset_ids),
                    ]
                }
            }

        datasets: dict[str, BigpictureBeaconDatasetResult] = {}
        async for bucket in iter_paged_buckets(
            self.client,
            self.index_name,
            page_clause,
            _FETCH_SIZE,
            sources=_DATASET_RECORD_SOURCES,
            # Fetches dataset title, description, and image count from one
            # representative document per bucket. The composite key alone
            # only has dataset_id and image_id, not these fields.
            sub_aggs=BigpictureDatasetBeaconService._SUB_AGGS,
        ):
            dataset_id = bucket["key"][BP_DATASET_ID_FIELD]
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
        self, query_clause: dict[str, Any], offset: int, limit: int
    ) -> BeaconResultSets[BigpictureBeaconImageResult]:
        image_sources = await take_page(
            iter_paged_documents(
                self.client,
                self.index_name,
                query_clause,
                _fetch_size(offset, limit),
                source_fields=[_IMAGE_ID_FIELD],
                sort_field=_IMAGE_ID_FIELD,
            ),
            offset,
            limit,
        )
        records = [
            BeaconResultSet[BigpictureBeaconImageResult](
                id=source[_IMAGE_ID_FIELD],
                setType="image",
                results=[BigpictureBeaconImageResult(imageId=source[_IMAGE_ID_FIELD])],
            )
            for source in image_sources
        ]
        return BeaconResultSets(resultSet=records)
