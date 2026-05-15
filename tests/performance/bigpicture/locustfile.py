import json
import random

from locust import HttpUser, task, between

from search_api.api.beacon.models import (
    BeaconQueryFilter,
    BeaconQueryRequest,
    BeaconQuery,
)

# locust -f tests/performance/bigpicture/locustfile.py --host=http://localhost:8000

QUERIES = [
    {
        "name": "Datasets match dataset_description",
        "body": BeaconQueryRequest(
            query=BeaconQuery(
                filters=[
                    BeaconQueryFilter(
                        id="dataset_description",
                        value="natural variation",
                    )
                ]
            )
        ).model_dump(exclude_none=True),
    },
    # Animal species
    {
        "name": "Datasets match species code 0.001% sensitivity",
        "body": BeaconQueryRequest(
            query=BeaconQuery(
                filters=[
                    BeaconQueryFilter(
                        id="animal_species",
                        value="outstanding",
                    )
                ]
            )
        ).model_dump(exclude_none=True),
    },
    {
        "name": "Datasets match species code 1% sensitivity",
        "body": BeaconQueryRequest(
            query=BeaconQuery(
                filters=[
                    BeaconQueryFilter(
                        id="animal_species",
                        value="1",
                    )
                ]
            )
        ).model_dump(exclude_none=True),
    },
    {
        "name": "Datasets match species code 83.9% sensitivity",
        "body": BeaconQueryRequest(
            query=BeaconQuery(
                filters=[
                    BeaconQueryFilter(
                        id="animal_species",
                        value="poor",
                    )
                ]
            )
        ).model_dump(exclude_none=True),
    },
    # Age at extraction
    {
        "name": "Datasets match age_at_extraction 8.001% sensitivity",
        "body": BeaconQueryRequest(
            query=BeaconQuery(
                filters=[
                    BeaconQueryFilter(
                        id="age_at_extraction",
                        value="1-2",
                    )
                ]
            )
        ).model_dump(exclude_none=True),
    },
    {
        "name": "Datasets match age_at_extraction 1% sensitivity",
        "body": BeaconQueryRequest(
            query=BeaconQuery(
                filters=[
                    BeaconQueryFilter(
                        id="age_at_extraction",
                        value="7-8",
                    )
                ]
            )
        ).model_dump(exclude_none=True),
    },
    {
        "name": "Datasets match age_at_extraction 83.9% sensitivity",
        "body": BeaconQueryRequest(
            query=BeaconQuery(
                filters=[
                    BeaconQueryFilter(
                        id="age_at_extraction",
                        value="13-100",
                    )
                ]
            )
        ).model_dump(exclude_none=True),
    },
    # Staining target
    {
        "name": "Datasets match staining target code 0.001% sensitivity",
        "body": BeaconQueryRequest(
            query=BeaconQuery(
                filters=[
                    BeaconQueryFilter(
                        id="staining_target",
                        value="outstanding",
                    )
                ]
            )
        ).model_dump(exclude_none=True),
    },
    {
        "name": "Datasets match staining target code 1% sensitivity",
        "body": BeaconQueryRequest(
            query=BeaconQuery(
                filters=[
                    BeaconQueryFilter(
                        id="staining_target",
                        value="1",
                    )
                ]
            )
        ).model_dump(exclude_none=True),
    },
    {
        "name": "Datasets match staining target code 83.9% sensitivity",
        "body": BeaconQueryRequest(
            query=BeaconQuery(
                filters=[
                    BeaconQueryFilter(
                        id="staining_target",
                        value="poor",
                    )
                ]
            )
        ).model_dump(exclude_none=True),
    },
]


class OpenSearchUser(HttpUser):
    wait_time = between(0.5, 2)

    @task
    def run_random_query(self):
        query = random.choice(QUERIES)

        response = self.client.post(
            "/query",
            json=query["body"],
            headers={"Content-Type": "application/json"},
            name=query["name"],
        )

        data = response.json()
        print(f"{json.dumps(data, indent=2)}")
