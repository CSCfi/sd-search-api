from search_api.api.bigpicture.services import OpenSearchBigpictureBeaconService


def test_query_datasets():
    query = OpenSearchBigpictureBeaconService.get_query(
        [{"id": "dataset_description", "value": "natural variation"}]
    )
    assert query == {
        "aggs": {
            "datasets": {
                "aggs": {
                    "dataset_metadata": {
                        "top_hits": {
                            "_source": [
                                "dataset_title",
                                "dataset_description",
                                "dataset_image_cnt",
                            ],
                            "size": 1,
                        }
                    }
                },
                "composite": {
                    "size": 1000,
                    "sources": [{"dataset_id": {"terms": {"field": "dataset_id"}}}],
                },
            }
        },
        "query": {
            "bool": {"must": [{"match": {"dataset_description": "natural variation"}}]}
        },
        "size": 0,
    }
