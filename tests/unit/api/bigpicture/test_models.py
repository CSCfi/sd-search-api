from typing import cast

from search_api.api.beacon.models import (
    BeaconQueryRequest,
    BeaconQuery,
    BeaconQueryGranularity,
    BeaconQueryFilter,
)
from search_api.services.validate import validate_json


def test_beacon_query_request():
    schema_url = "https://raw.githubusercontent.com/ga4gh-beacon/beacon-v2/refs/heads/main/framework/json/requests/beaconRequestBody.json"

    # Request without filters is Beacon V2 compatible.
    for granularity in ["boolean", "count", "record"]:
        request = BeaconQueryRequest(
            query=BeaconQuery(
                requestedGranularity=cast(BeaconQueryGranularity, granularity)
            )
        )

        validate_json(request.model_dump(), schema_url)

    # Request with filters is Beacon V2 compatible.
    for granularity in ["boolean", "count", "record"]:
        request = BeaconQueryRequest(
            query=BeaconQuery(
                requestedGranularity=cast(BeaconQueryGranularity, granularity),
                filters=[
                    BeaconQueryFilter(
                        id="test",
                        value="test",
                        operator="=",
                        includeDescendantTerms=False,
                    )
                ],
            )
        )

        validate_json(request.model_dump(), schema_url)
