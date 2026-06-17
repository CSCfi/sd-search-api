from typing import cast, get_args

from search_api.api.beacon.models import (
    BeaconQueryRequest,
    BeaconQuery,
    BeaconQueryGranularity,
    BeaconQueryFilter,
)
from search_api.api.bigpicture.models import (
    BP_FILTERING_TERMS,
    BP_FILTERING_TERMS_RESPONSE,
    BP_INFO_RESPONSE,
)
from search_api.bigpicture.models import (
    BigpictureBlockFields,
    BigpictureCodeAttributeValue,
    BigpictureStainingFields,
)
from search_api.services.validate import validate_json


def test_ontology_model_fields_match_filtering_terms():
    """BigpictureCodeAttributeValue field names and ontology filtering term ids must be identical."""

    def ontology_field_names(model_cls) -> set[str]:
        return {
            name
            for name, info in model_cls.model_fields.items()
            if info.annotation is BigpictureCodeAttributeValue
            or BigpictureCodeAttributeValue in get_args(info.annotation)
        }

    model_field_ids = ontology_field_names(
        BigpictureBlockFields
    ) | ontology_field_names(BigpictureStainingFields)
    filtering_term_ids = {
        t.id for t in BP_FILTERING_TERMS if t.type in ("ontology", "ontologyOrValue")
    }

    assert model_field_ids == filtering_term_ids


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


def test_filtering_terms_response():
    schema_url = "https://raw.githubusercontent.com/ga4gh-beacon/beacon-v2/refs/heads/main/framework/json/responses/beaconFilteringTermsResponse.json"

    # Filtering term response is Beacon V2 compatible.

    validate_json(BP_FILTERING_TERMS_RESPONSE.model_dump(exclude_none=True), schema_url)


def test_info_terms_response():
    schema_url = "https://raw.githubusercontent.com/ga4gh-beacon/beacon-v2/refs/heads/main/framework/json/responses/beaconInfoResponse.json"

    # Info term response is Beacon V2 compatible.

    validate_json(BP_INFO_RESPONSE.model_dump(exclude_none=True), schema_url)
