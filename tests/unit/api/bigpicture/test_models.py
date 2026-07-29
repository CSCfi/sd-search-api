import json
from pathlib import Path
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
    BP_NON_FILTERING_FIELDS,
    BP_INFO_RESPONSE,
)
from search_api.api.opensearch.index_generator import OpenSearchIndexGeneratorService
from search_api.api.opensearch.models import OpenSearchOntologyOrValue
from search_api.bigpicture.services.extract import (
    BigpictureCodeAttributeValue,
    BigpictureFields,
    BigpictureSpecimenFields,
    BigpictureStainingFields,
)
from search_api.services.validate import validate_json

# Map each OpenSearch nested-container path to the model that holds its fields.
_CONTAINER_MODELS = {
    "": BigpictureFields,
    "specimen": BigpictureSpecimenFields,
    "staining": BigpictureStainingFields,
}

_BP_INDEX_PATH = (
    Path(__file__).resolve().parents[4]
    / "search_api"
    / "opensearch"
    / "bigpicture"
    / "bp-image-index.json"
)


def test_ontology_model_fields_match_filtering_terms():
    """BigpictureCodeAttributeValue field names and ontology filtering term ids must be identical."""

    def ontology_field_names(model_cls) -> set[str]:
        return {
            name
            for name, info in model_cls.model_fields.items()
            if info.annotation is BigpictureCodeAttributeValue
            or BigpictureCodeAttributeValue in get_args(info.annotation)
        }

    model_field_ids = (
        ontology_field_names(BigpictureSpecimenFields)
        | ontology_field_names(BigpictureStainingFields)
        | ontology_field_names(BigpictureFields)  # top-level diagnosis fields
    )
    filtering_term_ids = {
        t.id for t in BP_FILTERING_TERMS if t.type in ("ontology", "ontologyOrValue")
    }

    assert model_field_ids == filtering_term_ids


def test_extracted_fields_match_filtering_terms():
    """Every filtering term must map to an extracted model attribute."""
    for term in BP_FILTERING_TERMS:
        osf = term.opensearch_field
        paths = (
            [osf.concept_value_field, osf.other_value_field]
            if isinstance(osf, OpenSearchOntologyOrValue)
            else [osf]
        )
        for path in paths:
            container, _, name = path.rpartition(".")
            model_cls = _CONTAINER_MODELS[container]
            assert name in model_cls.model_fields, (
                f"filtering-term field {name!r} is not an attribute of {model_cls.__name__}"
            )


def test_committed_index_matches_generated():
    """The committed index JSON must equal a fresh generation.

    Run ``python scripts/admin.py Bigpicture generate-index`` after changing the
    filtering terms or index-only fields.
    """
    generated = OpenSearchIndexGeneratorService(
        [*BP_NON_FILTERING_FIELDS, *BP_FILTERING_TERMS]
    ).generate()
    committed = json.loads(_BP_INDEX_PATH.read_text())
    assert committed == generated


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
