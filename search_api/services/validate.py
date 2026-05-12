import json
from typing import Any
from pathlib import Path

import requests
from jsonschema import validators, ValidationError


def _load_schema(schema_source: str) -> dict[str, Any]:
    """
    Load JSON schema from, URL or local file path.
    """

    if schema_source.startswith(("http://", "https://")):
        try:
            response = requests.get(schema_source, timeout=10)
            response.raise_for_status()
            return response.json()
        except requests.RequestException:
            raise ValidationError(f"Failed to fetch JSON schema: {schema_source}")
        except ValueError:
            raise ValidationError(f"Invalid JSON schema: {schema_source}")

    try:
        path = Path(schema_source)
        if not path.exists():
            raise ValidationError(f"JSON schema file not found: {schema_source}")

        return json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        raise ValidationError(f"Invalid JSON schema: {schema_source}")


def validate_json(data: dict, schema_source: str | dict[str, Any]):
    """
    Validate JSON data against a schema from URL, local file path or dict.
    """

    if isinstance(schema_source, str):
        schema_source = _load_schema(schema_source)

    validator_cls = validators.validator_for(schema_source)
    validator_cls.check_schema(schema_source)

    validator = validator_cls(schema_source)

    errors = sorted(validator.iter_errors(data), key=lambda e: list(e.path))

    if errors:
        message = "; ".join(
            f"{'.'.join(map(str, e.path)) or 'root'}: {e.message}" for e in errors
        )
        raise ValidationError(message)
