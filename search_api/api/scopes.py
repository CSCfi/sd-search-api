"""Load and validate declarative filtering scope configuration from YAML files."""

from collections.abc import Sequence
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError

from search_api.api.beacon.models import BeaconFilteringScope, BeaconFilteringTerm
from search_api.api.fields import _format_validation_error
from search_api.api.opensearch.models import OpenSearchField
from search_api.exceptions import ConfigurationException, UserException


SCOPE_FIELD = "scope"


def scope_field() -> OpenSearchField:
    """Returns the OpenSearch field a document's scope is indexed in."""
    return OpenSearchField(id=SCOPE_FIELD, type="keyword")


def validate_document_scope(
    scope: str | None, filtering_scopes: Sequence[BeaconFilteringScope]
) -> None:
    """Check an extracted document's scope against the declared scopes.

    A document must carry a scope when the deployment declares any, so that a
    scoped query cannot silently miss it.

    :raises UserException: if the scope is missing or is not declared.
    """
    if not filtering_scopes:
        return
    valid = {s.id for s in filtering_scopes}
    if scope is None:
        raise UserException(f"Missing scope; valid scopes: {sorted(valid)}.")
    if scope not in valid:
        raise UserException(
            f"Unsupported scope: {scope!r}. Valid scopes: {sorted(valid)}."
        )


class ScopesConfig(BaseModel):
    """Schema of scopes configuration YAML file."""

    model_config = ConfigDict(extra="forbid")

    filtering_scopes: list[BeaconFilteringScope]


def load_scopes_config(path: str | Path) -> ScopesConfig:
    """Read, parse, and validate a scopes YAML file.

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
        return ScopesConfig.model_validate(data)
    except ValidationError as e:
        raise ConfigurationException(_format_validation_error(path, e)) from e


def validate_filtering_scopes(
    filtering_terms: Sequence[BeaconFilteringTerm],
    filtering_scopes: Sequence[BeaconFilteringScope],
    source: str | Path,
) -> None:
    """Ensure every term's scopes reference defined filtering scopes.

    :raises ConfigurationException: if any term references an unknown scope.
    """
    scope_ids = {scope.id for scope in filtering_scopes}
    unknown = {
        term_scope
        for term in filtering_terms
        for term_scope in term.scopes
        if term_scope not in scope_ids
    }
    if unknown:
        raise ConfigurationException(
            f"'{source}': filtering terms reference unknown "
            f"scope(s) {sorted(unknown)}; defined scopes are {sorted(scope_ids)}."
        )
