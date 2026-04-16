import random
from locust import HttpUser, task, between

# locust -f tests/performance/bigpicture/locustfile.py --host=http://localhost:9200

INDEX = "bp-image-index"

QUERIES = [
    {
        "name": "match all",
        "body": {
            "query": {
                "match_all": {}
            }
        }
    },
    # Text search
    {
        "name": "match dataset_description",
        "body": {
            "query": {
                "query": {
                    "match": {
                        "dataset_description": "natural variation"
                    }
                }
            }
        }
    },
    # Code search
    {
        "name": "species code 0.001% sensitivity",
        "body": {
            "query": {
                "term": {
                    "species": "outstanding"
                }
            }
        }
    },
    {
        "name": "species code 0.01% sensitivity",
        "body": {
            "query": {
                "term": {
                    "species": "excellent"
                }
            }
        }
    },
    {
        "name": "species code 0.1% sensitivity",
        "body": {
            "query": {
                "term": {
                    "species": "high"
                }
            }
        }
    },
    {
        "name": "species code 1% sensitivity",
        "body": {
            "query": {
                "term": {
                    "species": "1"
                }
            }
        }
    },
    {
        "name": "species code 5% sensitivity",
        "body": {
            "query": {
                "term": {
                    "species": "4"
                }
            }
        }
    },
    {
        "name": "species code 10% sensitivity",
        "body": {
            "query": {
                "term": {
                    "species": "10"
                }
            }
        }
    },
    {
        "name": "species code 83.9% sensitivity",
        "body": {
            "query": {
                "term": {
                    "species": "poor"
                }
            }
        }
    },
    # Age at extraction
    {
        "name": "age_at_extraction 0.001% sensitivity",
        "body": {
            "query": {
                "range": {
                    "age_at_extraction": {
                        "gte": 1,
                        "lte": 2
                    }
                }
            }
        }
    },
    {
        "name": "age_at_extraction 0.01% sensitivity",
        "body": {
            "query": {
                "range": {
                    "age_at_extraction": {
                        "gte": 3,
                        "lte": 4
                    }
                }
            }
        }
    },
    {
        "name": "age_at_extraction 0.1% sensitivity",
        "body": {
            "query": {
                "range": {
                    "age_at_extraction": {
                        "gte": 5,
                        "lte": 6
                    }
                }
            }
        }
    },
    {
        "name": "age_at_extraction 1% sensitivity",
        "body": {
            "query": {
                "range": {
                    "age_at_extraction": {
                        "gte": 7,
                        "lte": 8
                    }
                }
            }
        }
    },
    {
        "name": "age_at_extraction 5% sensitivity",
        "body": {
            "query": {
                "range": {
                    "age_at_extraction": {
                        "gte": 9,
                        "lte": 10
                    }
                }
            }
        }
    },
    {
        "name": "age_at_extraction 10% sensitivity",
        "body": {
            "query": {
                "range": {
                    "age_at_extraction": {
                        "gte": 11,
                        "lte": 12
                    }
                }
            }
        }
    },
    {
        "name": "age_at_extraction 83.9% sensitivity",
        "body": {
            "query": {
                "range": {
                    "age_at_extraction": {
                        "gte": 13,
                        "lte": 100
                    }
                }
            }
        }
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
