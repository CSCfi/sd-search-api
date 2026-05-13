import json
from typing import Any

import fsspec
from jsonschema import validators, ValidationError
from referencing import Registry, Resource


def load_json(uri: str) -> dict[str, Any]:
    """
    Load JSON  from, URL or local file path.
    """
    with fsspec.open(uri) as f:
        return json.load(f)


def make_registry() -> Registry:
    def retrieve(uri: str) -> Resource:
        schema = load_json(uri)
        return Resource.from_contents(schema)

    return Registry(retrieve=retrieve)


def validate_json(data: dict, schema_source: str | dict[str, Any]):
    """
    Validate JSON data against a schema from URL, local file path or dict.
    """

    if isinstance(schema_source, str):
        schema = load_json(schema_source)
        schema["$id"] = schema_source
    else:
        schema = schema_source

    validator_cls = validators.validator_for(schema)
    validator_cls.check_schema(schema)

    validator = validator_cls(schema, registry=make_registry())

    errors = sorted(validator.iter_errors(data), key=lambda e: list(e.path))

    if errors:
        message = "; ".join(
            f"{'.'.join(map(str, e.path)) or 'root'}: {e.message}" for e in errors
        )
        raise ValidationError(message)
