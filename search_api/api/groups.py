"""Load and validate declarative filtering group configuration from YAML files."""

from collections.abc import Sequence
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError

from search_api.api.beacon.models import BeaconFilteringGroup, BeaconFilteringTerm
from search_api.api.fields import _format_validation_error
from search_api.exceptions import ConfigurationException


class GroupsConfig(BaseModel):
    """Schema of groups configuration YAML file."""

    model_config = ConfigDict(extra="forbid")

    filtering_groups: list[BeaconFilteringGroup]


def load_groups_config(path: str | Path) -> GroupsConfig:
    """Read, parse, and validate a groups YAML file.

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
        return GroupsConfig.model_validate(data)
    except ValidationError as e:
        raise ConfigurationException(_format_validation_error(path, e)) from e


def validate_ui_groups(
    filtering_terms: Sequence[BeaconFilteringTerm],
    filtering_groups: Sequence[BeaconFilteringGroup],
    source: str | Path,
) -> None:
    """Ensure every term's ui_group references a defined filtering group.

    :raises ConfigurationException: if any term references an unknown ui_group.
    """
    group_ids = {group.id for group in filtering_groups}
    unknown = {
        term.ui_group
        for term in filtering_terms
        if term.ui_group is not None and term.ui_group not in group_ids
    }
    if unknown:
        raise ConfigurationException(
            f"'{source}': filtering terms reference unknown "
            f"ui_group(s) {sorted(unknown)}; defined groups are {sorted(group_ids)}."
        )
