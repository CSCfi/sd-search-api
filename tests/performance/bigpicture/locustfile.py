import json
import random
from locust import HttpUser, task, between

from search_api.api.beacon.models import BeaconQueryFilter
from search_api.api.bigpicture.services import (
    OpenSearchBigpictureBeaconService,
    BP_OPENSEARCH_INDEX,
)

# locust -f tests/performance/bigpicture/locustfile.py --host=http://localhost:9200

QUERIES = [
    {
        "name": "Datasets match dataset_description",
        "body": OpenSearchBigpictureBeaconService.get_query(
            [BeaconQueryFilter(id="dataset_description", value="natural variation")]
        ),
    },
    # Block
    {
        "name": "Datasets match species code 0.001% sensitivity",
        "body": OpenSearchBigpictureBeaconService.get_query(
            [BeaconQueryFilter(id="animal_species", value="outstanding")]
        ),
    },
    {
        "name": "Datasets match species code 1% sensitivity",
        "body": OpenSearchBigpictureBeaconService.get_query(
            [BeaconQueryFilter(id="animal_species", value="1")]
        ),
    },
    {
        "name": "Datasets match species code 83.9% sensitivity",
        "body": OpenSearchBigpictureBeaconService.get_query(
            [BeaconQueryFilter(id="animal_species", value="poor")]
        ),
    },
    {
        "name": "Datasets match age_at_extraction 8.001% sensitivity",
        "body": OpenSearchBigpictureBeaconService.get_query(
            [BeaconQueryFilter(id="age_at_extraction", value="1-2")]
        ),
    },
    {
        "name": "Datasets match age_at_extraction 1% sensitivity",
        "body": OpenSearchBigpictureBeaconService.get_query(
            [BeaconQueryFilter(id="age_at_extraction", value="7-8")]
        ),
    },
    {
        "name": "Datasets match age_at_extraction 83.9% sensitivity",
        "body": OpenSearchBigpictureBeaconService.get_query(
            [BeaconQueryFilter(id="age_at_extraction", value="13-100")]
        ),
    },
    # Staining
    {
        "name": "Datasets match staining target code 0.001% sensitivity",
        "body": OpenSearchBigpictureBeaconService.get_query(
            [BeaconQueryFilter(id="staining_target", value="outstanding")]
        ),
    },
    {
        "name": "Datasets match staining target code 1% sensitivity",
        "body": OpenSearchBigpictureBeaconService.get_query(
            [BeaconQueryFilter(id="staining_target", value="1")]
        ),
    },
    {
        "name": "Datasets match staining target code 83.9% sensitivity",
        "body": OpenSearchBigpictureBeaconService.get_query(
            [BeaconQueryFilter(id="staining_target", value="poor")]
        ),
    },
]


class OpenSearchUser(HttpUser):
    wait_time = between(0.5, 2)

    @task
    def run_random_query(self):
        query = random.choice(QUERIES)

        response = self.client.post(
            f"/{BP_OPENSEARCH_INDEX}/_search",
            json=query["body"],
            headers={"Content-Type": "application/json"},
            name=query["name"],
        )

        data = response.json()
        total = data.get("hits", {}).get("total", {})
        count = total.get("value", 0)
        print(
            f"{response.status_code} {query['name']} -> matches: {count}\n{json.dumps(data, indent=2)}"
        )
