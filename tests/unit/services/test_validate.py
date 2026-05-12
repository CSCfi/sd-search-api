import pytest
from jsonschema import ValidationError

from search_api.services.validate import validate_json


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
