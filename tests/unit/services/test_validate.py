import pytest
from jsonschema import ValidationError

from search_api.services.validate import validate_json, load_json


def test_validate_json_valid():
    schema = {
        "type": "object",
        "properties": {"id": {"type": "integer"}},
        "required": ["id"],
    }

    data = {"id": 1}

    validate_json(data, schema)


def test_validate_json_invalid():
    schema = {
        "type": "object",
        "properties": {"id": {"type": "integer"}},
        "required": ["id"],
    }

    data = {"id": "a"}

    with pytest.raises(ValidationError) as exc:
        validate_json(data, schema)

    assert "id" in str(exc.value)


def test_valid_beacon_request_body():
    schema_url = "https://raw.githubusercontent.com/ga4gh-beacon/beacon-v2/refs/heads/main/framework/json/requests/beaconRequestBody.json"
    document_uri = "https://raw.githubusercontent.com/ga4gh-beacon/beacon-v2/refs/heads/main/framework/json/requests/examples-fullDocuments/beaconRequestBody-MIN-example.json"
    validate_json(load_json(document_uri), schema_url)
    document_uri = "https://raw.githubusercontent.com/ga4gh-beacon/beacon-v2/refs/heads/main/framework/json/requests/examples-fullDocuments/beaconRequestBody-MAX-example.json"
    validate_json(load_json(document_uri), schema_url)


def test_invalid_beacon_request_body():
    schema_url = "https://raw.githubusercontent.com/ga4gh-beacon/beacon-v2/refs/heads/main/framework/json/requests/beaconRequestBody.json"

    with pytest.raises(ValidationError) as exc_info:
        validate_json({}, schema_url)

    assert "'meta' is a required property" in str(exc_info.value)

    with pytest.raises(ValidationError) as exc_info:
        validate_json({"meta": {}}, schema_url)

    assert "meta: 'apiVersion' is a required property" in str(exc_info.value)
