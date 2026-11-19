from typing import Any, override
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

    @staticmethod
    def _build_dataset(
        dataset_id: str, fields: dict[str, Any]
    ) -> BigpictureBeaconDatasetResult:
        for f in _DATASET_OTHER_FIELDS:
            if f not in fields:
                raise SystemException(f"Dataset '{dataset_id}' is missing field: {f}")
        return BigpictureBeaconDatasetResult(
            datasetId=dataset_id,
            datasetTitle=fields["dataset_title"],
            datasetDescription=fields["dataset_description"],
            datasetUrl=f"https://datasets.bigpicture.eu/datasets/{quote(dataset_id.lower(), safe='')}.html",
            totalImageCount=fields["dataset_image_cnt"],
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

        # The bucket keys give each dataset's matching images.
        image_ids_by_dataset: dict[str, list[str]] = {}
        async for bucket in iter_paged_buckets(
            self.client,
            self.index_name,
            page_clause,
            _FETCH_SIZE,
            sources=_DATASET_RECORD_SOURCES,
        ):
            image_ids_by_dataset.setdefault(
                bucket["key"][BP_DATASET_ID_FIELD], []
            ).append(bucket["key"][_IMAGE_ID_FIELD])

        dataset_fields = await self._get_dataset_fields(
            [image_ids[0] for image_ids in image_ids_by_dataset.values()]
        )

        results: BeaconResultSets[BigpictureBeaconDatasetResult] = BeaconResultSets()
        for dataset_id, image_ids in image_ids_by_dataset.items():
            if dataset_id not in dataset_fields:
                # A sync deleted the dataset's first image after its images were read.
                continue
            result = BigpictureDatasetBeaconService._build_dataset(
                dataset_id, dataset_fields[dataset_id]
            )
            result.imageIds = image_ids
            result.matchingImageCount = len(image_ids)
            results.resultSet.append(
                BeaconResultSet[BigpictureBeaconDatasetResult](
                    id=dataset_id, results=[result]
                )
            )
        return results

    async def _get_dataset_fields(
        self, image_ids: list[str]
    ) -> dict[str, dict[str, Any]]:
        """Read the dataset fields from the image documents.

        Every image document of a dataset has the same dataset fields, so one
        image per dataset is enough.

        :param image_ids: One image of each dataset to read.
        :return: The dataset fields by dataset id.
        """
        dataset_fields: dict[str, dict[str, Any]] = {}
        for i in range(0, len(image_ids), _FETCH_SIZE):
            batch = image_ids[i : i + _FETCH_SIZE]
            resp = await self.client.search(
                index=self.index_name,
                body={
                    "size": len(batch),
                    "query": build_terms_clause(_IMAGE_ID_FIELD, batch),
                    "_source": [BP_DATASET_ID_FIELD, *_DATASET_OTHER_FIELDS],
                },
            )
            for hit in resp["hits"]["hits"]:
                source = hit["_source"]
                dataset_fields[source[BP_DATASET_ID_FIELD]] = source
        return dataset_fields


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
