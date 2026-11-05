from search_api.api.bigpicture.opensearch import (
    _FETCH_SIZE,
    BigpictureDatasetBeaconService,
    _fetch_size,
)


def _bucket() -> dict:
    """One dataset's bucket, as /datasets gets it from OpenSearch.

    A bucket is one group of an OpenSearch aggregation. Here, the images of one
    dataset. Its dataset_info sub-aggregation returns one of those images, which
    has the dataset fields _build_dataset reads.
    """
    source = {
        "dataset_title": "Title",
        "dataset_description": "Description",
        "dataset_image_cnt": 1,
    }
    return {"dataset_info": {"hits": {"hits": [{"_source": source}]}}}


def test_dataset_url_escapes_dataset_id():
    result = BigpictureDatasetBeaconService._build_dataset("../a/b?c#d", _bucket())
    assert (
        result.datasetUrl
        == "https://datasets.bigpicture.eu/datasets/..%2Fa%2Fb%3Fc%23d.html"
    )


def test_fetch_size():
    assert _fetch_size(offset=20, limit=10) == 30
    assert _fetch_size(offset=_FETCH_SIZE, limit=10) == _FETCH_SIZE
    assert _fetch_size(offset=0, limit=0) == _FETCH_SIZE
