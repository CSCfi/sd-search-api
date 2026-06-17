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
from search_api.bigpicture.models import (
    BigpictureBlockFields,
    BigpictureCodeAttributeValue,
    BigpictureStainingFields,
)
from search_api.services.validate import validate_json

# The nested OpenSearch containers and the models whose fields they store.
_CONTAINER_MODELS = {
    "blocks": BigpictureBlockFields,
    "stains": BigpictureStainingFields,
}

_BP_INDEX_PATH = (
    Path(__file__).resolve().parents[4]
    / "search_api"
    / "opensearch"
    / "bigpicture"
    / "bp-image-index.json"
)


def _opensearch_field_paths(opensearch_field) -> list[str]:
    """Return all OpenSearch field paths referenced by a filtering term."""
    if isinstance(opensearch_field, OpenSearchOntologyOrValue):
        return [
            opensearch_field.concept_value_field,
            opensearch_field.other_value_field,
        ]
    return [opensearch_field]


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


def test_opensearch_mapping_matches_model_fields():
    """The nested OpenSearch mapping must declare exactly the block/stain model fields.

    Catches both stale mapping keys (no model field) and unmapped model fields
    (which would be indexed with a guessed type instead of the intended one).
    """
    properties = json.loads(_BP_INDEX_PATH.read_text())["mappings"]["properties"]
    for container, model_cls in _CONTAINER_MODELS.items():
        mapped = set(properties[container]["properties"])
        assert mapped == set(model_cls.model_fields), (
            f"{container} mapping keys {mapped} != "
            f"{model_cls.__name__} fields {set(model_cls.model_fields)}"
        )


def test_filtering_term_opensearch_fields_match_model_fields():
    """Every filtering term's OpenSearch field path must resolve to a real model field."""
    for term in BP_FILTERING_TERMS:
        for path in _opensearch_field_paths(term.opensearch_field):
            container, _, attr = path.partition(".")
            if container not in _CONTAINER_MODELS:
                continue  # top-level field (e.g. dataset_title)
            model_cls = _CONTAINER_MODELS[container]
            assert attr in model_cls.model_fields, (
                f"{term.id}: opensearch_field '{path}' has no matching "
                f"{model_cls.__name__} attribute"
            )


def test_committed_index_matches_generated():
    """The committed index JSON must equal a fresh generation.

    Run ``python scripts/bigpicture.py generate-index`` after changing the
    filtering terms or index-only fields.
    """
    generated = OpenSearchIndexGeneratorService(
        BP_FILTERING_TERMS, BP_NON_FILTERING_FIELDS
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
