from typing import Any

import pytest

from search_api.api.beacon.models import BeaconQueryFilter, BeaconQueryPagination
from search_api.api.bigpicture import opensearch as bp_opensearch
from search_api.api.bigpicture.models import BP_FILTERING_SCOPES, BP_FILTERING_TERMS
from search_api.api.bigpicture.opensearch import (
    BigpictureDatasetBeaconService,
    BigpictureImageBeaconService,
)
from tests.integration.conftest import bp_search

_IMAGES_BY_DATASET = {
    "ds_1": ["image_1", "image_2"],
    "ds_2": ["image_3"],
    "ds_3": ["image_4", "image_5"],
}
# ds_2 is non-clinical, so a diagnosis filter does not constrain it.
_NON_CLINICAL_DATASET = "ds_2"
# The diagnosis of each clinical image.
_DIAGNOSIS = "73211009"
_OTHER_DIAGNOSIS = "38341003"
_DIAGNOSIS_BY_IMAGE = {
    "image_1": _DIAGNOSIS,
    "image_2": _OTHER_DIAGNOSIS,
    "image_4": _OTHER_DIAGNOSIS,
    "image_5": _OTHER_DIAGNOSIS,
}


@pytest.fixture(scope="module")
def bp_opensearch_docs() -> list[dict[str, Any]]:
    """Bigpicture image documents for the test index, one for each image in each dataset."""
    return [
        {
            "image_id": image_id,
            "dataset_id": dataset_id,
            "dataset_image_cnt": len(image_ids),
            "dataset_title": dataset_id,
            "dataset_description": dataset_id,
            "scope": "non_clinical"
            if dataset_id == _NON_CLINICAL_DATASET
            else "clinical",
            "observation": [{"diagnosis": _DIAGNOSIS_BY_IMAGE[image_id]}]
            if image_id in _DIAGNOSIS_BY_IMAGE
            else [],
        }
        for dataset_id, image_ids in _IMAGES_BY_DATASET.items()
        for image_id in image_ids
    ]


@pytest.fixture(autouse=True)
def _small_fetches(monkeypatch):
    # One record per OpenSearch request, so a page spans several requests.
    monkeypatch.setattr(bp_opensearch, "_FETCH_SIZE", 1)


@pytest.fixture
def image_service(bp_opensearch_index_name: str) -> BigpictureImageBeaconService:
    return BigpictureImageBeaconService(
        client=bp_search,
        index_name=bp_opensearch_index_name,
        filtering_terms=BP_FILTERING_TERMS,
        filtering_scopes=BP_FILTERING_SCOPES,
    )


@pytest.fixture
def dataset_service(bp_opensearch_index_name: str) -> BigpictureDatasetBeaconService:
    return BigpictureDatasetBeaconService(
        client=bp_search,
        index_name=bp_opensearch_index_name,
        filtering_terms=BP_FILTERING_TERMS,
        filtering_scopes=BP_FILTERING_SCOPES,
    )


def _pagination(skip: int, limit: int) -> BeaconQueryPagination:
    return BeaconQueryPagination(skip=skip, limit=limit)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("skip", "limit", "expected"),
    [
        (0, 2, ["image_1", "image_2"]),
        (1, 2, ["image_3", "image_4"]),
        (2, 2, ["image_5"]),
        (3, 2, []),
        # A limit of 0 returns every image, and the skip is ignored.
        (0, 0, ["image_1", "image_2", "image_3", "image_4", "image_5"]),
        (2, 0, ["image_1", "image_2", "image_3", "image_4", "image_5"]),
    ],
)
async def test_image_pages(bp_opensearch_index, image_service, skip, limit, expected):
    result = await image_service.query(
        filters=[], granularity="record", pagination=_pagination(skip, limit)
    )
    # The total counts every matching image, whatever the page.
    assert result.total == 5
    assert [rs.id for rs in result.result_sets.resultSet] == expected


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("skip", "limit", "expected"),
    [
        (0, 2, ["ds_1", "ds_2"]),
        (1, 2, ["ds_3"]),
        (2, 2, []),
        # A limit of 0 returns every dataset, and the skip is ignored.
        (0, 0, ["ds_1", "ds_2", "ds_3"]),
        (2, 0, ["ds_1", "ds_2", "ds_3"]),
    ],
)
async def test_dataset_pages(
    bp_opensearch_index, dataset_service, skip, limit, expected
):
    result = await dataset_service.query(
        filters=[], granularity="record", pagination=_pagination(skip, limit)
    )
    # A page is a page of datasets in dataset id order, each with all of its
    # matching images.
    assert result.total == 3
    datasets = [
        (rs.id, sorted(rs.results[0].imageIds)) for rs in result.result_sets.resultSet
    ]
    assert datasets == [(d, _IMAGES_BY_DATASET[d]) for d in expected]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("skip", "expected"),
    [(0, [("ds_1", ["image_1"])]), (1, [("ds_2", ["image_3"])]), (2, [])],
)
async def test_dataset_pages_scope_specific_filter(
    bp_opensearch_index, dataset_service, skip, expected
):
    """Page datasets with a filter on diagnosis, a clinical image filter only.

    A filter on a clinical field applies only to clinical images. Of the clinical
    images, only image_1 of ds_1 has the diagnosis. So two datasets match: clinical
    dataset ds_1, listing image_1, and non-clinical dataset ds_2, listing image_3.
    """
    result = await dataset_service.query(
        filters=[BeaconQueryFilter(id="diagnosis", value=_DIAGNOSIS)],
        granularity="record",
        pagination=_pagination(skip, 1),
    )
    assert result.total == 2
    datasets = [
        (rs.id, sorted(rs.results[0].imageIds)) for rs in result.result_sets.resultSet
    ]
    assert datasets == expected
