"""Load and validate declarative filtering qualifier configuration from YAML files."""

from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError

from search_api.api.beacon.models import BeaconFilteringQualifier
from search_api.api.fields import _format_validation_error
from search_api.api.opensearch.models import OpenSearchField
from search_api.exceptions import ConfigurationException, UserException

# The single indexed field holding every qualifier value of a nested group item.
# One field per group, present in every nested group, so adding a qualifier or
# applying an existing one to another group needs no index change.
QUALIFIERS_FIELD = "qualifiers"

# Separates a qualifier id from its value in an indexed value and in the
# ``qualifier=<id>:<value>`` query parameter.
QUALIFIER_VALUE_SEPARATOR = ":"


class QualifiersConfig(BaseModel):
    """Schema of qualifiers configuration YAML file."""

    model_config = ConfigDict(extra="forbid")

    filtering_qualifiers: list[BeaconFilteringQualifier]


def load_qualifiers_config(path: str | Path) -> QualifiersConfig:
    """Read, parse, and validate a qualifiers YAML file.

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
        return QualifiersConfig.model_validate(data)
    except ValidationError as e:
        raise ConfigurationException(_format_validation_error(path, e)) from e


def encode_qualifier_value(qualifier_id: str, value: str) -> str:
    """Encode one qualifier value as it is indexed: ``<qualifier id>:<value>``.

    Every qualifier of a group shares one indexed field, so the qualifier id has
    to travel with the value.
    """
    return f"{qualifier_id}{QUALIFIER_VALUE_SEPARATOR}{value}"


def decode_qualifier_value(encoded: str) -> tuple[str, str]:
    """Split an encoded qualifier value back into its id and value."""
    qualifier_id, _, value = encoded.partition(QUALIFIER_VALUE_SEPARATOR)
    return qualifier_id, value


def qualifier_fields(groups: Iterable[str]) -> list[OpenSearchField]:
    """Returns the qualifiers field of each nested group.

    Emitted for every nested group rather than only the qualified ones, so that
    declaring a qualifier over a new group is a config change alone. It is
    multivalued because a group item carries every qualifier value it was stated
    under, across all of its qualifiers.
    """
    return [
        OpenSearchField(
            id=QUALIFIERS_FIELD, type="keyword", group=group, multivalued=True
        )
        for group in sorted(groups)
    ]


def validate_filtering_qualifiers(
    filtering_terms: Sequence[OpenSearchField],
    non_filtering_fields: Sequence[OpenSearchField],
    filtering_qualifiers: Sequence[BeaconFilteringQualifier],
    source: str | Path,
) -> None:
    """Ensure every qualifier names groups that filtering terms actually define.

    A qualifier is indexed alongside the values of its groups, so a qualifier
    naming a group that does not exist would never be indexed and would silently
    filter nothing.

    :raises ConfigurationException: if a qualifier names an unknown group, or two
        qualifiers share an id.
    """
    # Groups come from the filtering terms.
    groups = {term.group for term in filtering_terms if term.group is not None}

    unknown = {
        group
        for qualifier in filtering_qualifiers
        for group in qualifier.groups
        if group not in groups
    }
    if unknown:
        raise ConfigurationException(
            f"'{source}': filtering qualifiers reference unknown "
            f"group(s) {sorted(unknown)}; defined groups are {sorted(groups)}."
        )

    ids = [qualifier.id for qualifier in filtering_qualifiers]
    duplicates = {id_ for id_ in ids if ids.count(id_) > 1}
    if duplicates:
        raise ConfigurationException(
            f"'{source}': filtering qualifier id(s) {sorted(duplicates)} defined more than once."
        )

    # Every nested group holds its qualifier values in QUALIFIERS_FIELD, so any
    # indexed field of that id in a group would map to the same path and be
    # silently overwritten when the document is built.
    clashes = sorted(
        f"{field.group}.{field.id}"
        for field in (*filtering_terms, *non_filtering_fields)
        if field.id == QUALIFIERS_FIELD and field.group is not None
    )
    if clashes:
        raise ConfigurationException(
            f"'{source}': field(s) {clashes} collide with the reserved "
            f"qualifiers field {QUALIFIERS_FIELD!r}."
        )

    separators = sorted(
        f"{qualifier.id}={value}"
        for qualifier in filtering_qualifiers
        for value in (qualifier.id, *qualifier.values)
        if QUALIFIER_VALUE_SEPARATOR in value
    )
    if separators:
        raise ConfigurationException(
            f"'{source}': filtering qualifier id(s)/value(s) {separators} contain "
            f"the reserved separator {QUALIFIER_VALUE_SEPARATOR!r}."
        )


def validate_requested_qualifiers(
    qualifiers: Mapping[str, Sequence[str]],
    filtering_qualifiers: Sequence[BeaconFilteringQualifier],
) -> None:
    """Reject qualifier ids and values that the deployment does not declare."""
    values_by_id = {q.id: set(q.values) for q in filtering_qualifiers}
    for qualifier_id, values in qualifiers.items():
        if qualifier_id not in values_by_id:
            raise UserException(
                f"Unsupported qualifier: {qualifier_id!r}. "
                f"Valid qualifiers: {sorted(values_by_id)}."
            )
        if len(values) > 1:
            raise UserException(
                f"Several values {sorted(values)} for qualifier {qualifier_id!r}. "
                "Only one value can be provided for a qualifier."
            )
        unknown = sorted(set(values) - values_by_id[qualifier_id])
        if unknown:
            raise UserException(
                f"Unsupported value(s) {unknown} for qualifier {qualifier_id!r}. "
                f"Valid values: {sorted(values_by_id[qualifier_id])}."
            )
