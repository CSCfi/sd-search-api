import random
from locust import HttpUser, task, between

# locust -f tests/performance/bigpicture/locustfile.py --host=http://localhost:9200

INDEX = "bp-image-index"

QUERIES = [
    {
        "name": "Top 100 datasets match dataset_description",
        "body": {
            "size": 0,  # Return only dataset aggregation and not individual image documents.
            "query": {"match": {"dataset_description": "natural variation"}},
            # Aggregate documents by dataset id.
            "aggs": {
                "datasets": {
                    "terms": {
                        "field": "dataset_id",
                        "size": 100,  # Top 100 datasets with most images.
                    },
                    # Compute aggregation metrics for grouped dataset ids.
                    "aggs": {
                        # Return one representative document per dataset. Number
                        # of matched images is returned in 'doc_count' field
                        # for each 'key' (dataset id) field.
                        "dataset_metadata": {
                            "top_hits": {
                                "size": 1,
                                "_source": {
                                    "includes": [
                                        "dataset_short_name",
                                        "dataset_title",
                                        "dataset_description",
                                        "dataset_image_cnt",
                                    ]
                                },
                            }
                        }
                    },
                }
            },
        },
    },
    {"name": "images match all", "body": {"query": {"match_all": {}}}},
    # Text search
    {
        "name": "images match dataset_description",
        "body": {
            "query": {"query": {"match": {"dataset_description": "natural variation"}}}
        },
    },
    # Code search
    {
        "name": "images match species code 0.001% sensitivity",
        "body": {"query": {"term": {"species": "outstanding"}}},
    },
    {
        "name": "images match species code 0.01% sensitivity",
        "body": {"query": {"term": {"species": "excellent"}}},
    },
    {
        "name": "images match species code 0.1% sensitivity",
        "body": {"query": {"term": {"species": "high"}}},
    },
    {
        "name": "images match species code 1% sensitivity",
        "body": {"query": {"term": {"species": "1"}}},
    },
    {
        "name": "images match species code 5% sensitivity",
        "body": {"query": {"term": {"species": "5"}}},
    },
    {
        "name": "images match species code 10% sensitivity",
        "body": {"query": {"term": {"species": "10"}}},
    },
    {
        "name": "images match species code 83.9% sensitivity",
        "body": {"query": {"term": {"species": "poor"}}},
    },
    # Age at extraction
    {
        "name": "images match age_at_extraction 0.001% sensitivity",
        "body": {"query": {"range": {"age_at_extraction": {"gte": 1, "lte": 2}}}},
    },
    {
        "name": "images match age_at_extraction 0.01% sensitivity",
        "body": {"query": {"range": {"age_at_extraction": {"gte": 3, "lte": 4}}}},
    },
    {
        "name": "images match age_at_extraction 0.1% sensitivity",
        "body": {"query": {"range": {"age_at_extraction": {"gte": 5, "lte": 6}}}},
    },
    {
        "name": "images match age_at_extraction 1% sensitivity",
        "body": {"query": {"range": {"age_at_extraction": {"gte": 7, "lte": 8}}}},
    },
    {
        "name": "images match age_at_extraction 5% sensitivity",
        "body": {"query": {"range": {"age_at_extraction": {"gte": 9, "lte": 10}}}},
    },
    {
        "name": "images match age_at_extraction 10% sensitivity",
        "body": {"query": {"range": {"age_at_extraction": {"gte": 11, "lte": 12}}}},
    },
    {
        "name": "images match age_at_extraction 83.9% sensitivity",
        "body": {"query": {"range": {"age_at_extraction": {"gte": 13, "lte": 100}}}},
    },
    # Staining
    {
        "name": "images match by staining target 0.001% sensitivity",
        "body": {
            "query": {
                "nested": {
                    "path": "stains",
                    "query": {
                        "bool": {
                            "must": [
                                {"term": {"stains.staining_target": "outstanding"}}
                            ]
                        }
                    },
                }
            }
        },
    },
    {
        "name": "images match by staining target 0.01% sensitivity",
        "body": {
            "query": {
                "nested": {
                    "path": "stains",
                    "query": {
                        "bool": {
                            "must": [{"term": {"stains.staining_target": "excellent"}}]
                        }
                    },
                }
            }
        },
    },
    {
        "name": "images match by staining target 0.1% sensitivity",
        "body": {
            "query": {
                "nested": {
                    "path": "stains",
                    "query": {
                        "bool": {"must": [{"term": {"stains.staining_target": "high"}}]}
                    },
                }
            }
        },
    },
    {
        "name": "images match by staining target 1% sensitivity",
        "body": {
            "query": {
                "nested": {
                    "path": "stains",
                    "query": {
                        "bool": {"must": [{"term": {"stains.staining_target": "1"}}]}
                    },
                }
            }
        },
    },
    {
        "name": "images match by staining target 5% sensitivity",
        "body": {
            "query": {
                "nested": {
                    "path": "stains",
                    "query": {
                        "bool": {"must": [{"term": {"stains.staining_target": "5"}}]}
                    },
                }
            }
        },
    },
    {
        "name": "images match by staining target 10% sensitivity",
        "body": {
            "query": {
                "nested": {
                    "path": "stains",
                    "query": {
                        "bool": {"must": [{"term": {"stains.staining_target": "10"}}]}
                    },
                }
            }
        },
    },
    {
        "name": "images match by staining target 83.9% sensitivity",
        "body": {
            "query": {
                "nested": {
                    "path": "stains",
                    "query": {
                        "bool": {"must": [{"term": {"stains.staining_target": "poor"}}]}
                    },
                }
            }
        },
    },
]


class OpenSearchUser(HttpUser):
    wait_time = between(0.5, 2)

    @task
    def run_random_query(self):
        query = random.choice(QUERIES)

        self.client.post(
            f"/{INDEX}/_search",
            json=query["body"],
            headers={"Content-Type": "application/json"},
            name=query["name"],
        )
