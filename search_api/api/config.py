"""Load and validate declarative field configuration from YAML files."""

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from search_api.api.opensearch.models import (
    OpenSearchBeaconFilteringTerm,
    OpenSearchField,
)
from search_api.exceptions import ConfigurationException


class FieldsConfig(BaseModel):
    """Schema of a deployment fields YAML file."""

    # Reject unknown keys so config errors surface.
    model_config = ConfigDict(extra="forbid")

    filtering_terms: list[OpenSearchBeaconFilteringTerm]
    non_filtering_fields: list[OpenSearchField] = Field(default_factory=list)


def load_fields_config(path: str | Path) -> FieldsConfig:
    """Read, parse, and validate a fields YAML file.

    :raises ConfigurationException: if the file cannot be read, is not valid
    YAML, or does not match the expected schema.
    """
    path = Path(path)

    try:
        f = path.open()
    except OSError as e:
        raise ConfigurationException(
            f"Cannot read config file '{path}': {e.strerror}."
        ) from e

    try:
        data = yaml.safe_load(f)
    except yaml.YAMLError as e:
        raise ConfigurationException(f"YAML error in '{path}':\n{e}") from e

    if not isinstance(data, dict):
        raise ConfigurationException(f"Invalid config file '{path}'")

    try:
        return FieldsConfig.model_validate(data)
    except ValidationError as e:
        raise ConfigurationException(_format_validation_error(path, e)) from e


def _format_validation_error(path: Path, error: ValidationError) -> str:
    """Render Pydantic validation errors as a file-scoped, location-pointed list."""
    lines = [f"'{path}': {error.error_count()} configuration error(s):"]
    for err in error.errors(include_url=False):
        loc = ""
        for part in err["loc"]:
            loc += (
                f"[{part}]"
                if isinstance(part, int)
                else (f".{part}" if loc else str(part))
            )
        line = f"  - {loc or '<root>'}: {err['msg']}"
        # For a missing key the input is the whole parent object, which is noise;
        # for every other error the offending value is what the author needs.
        if err["type"] != "missing":
            line += f" (got {err['input']!r})"
        lines.append(line)
    return "\n".join(lines)
