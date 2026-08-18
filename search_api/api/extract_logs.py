from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict

from search_api.severity import LogSeverity


class ExtractLog(BaseModel):
    """One problem an extraction found."""

    model_config = ConfigDict(frozen=True)

    severity: LogSeverity
    message: str
    field_id: str | None = None


def invalid_scheme_log(
    field_id: str,
    concept_id: str | None,
    meaning: str | None,
    scheme: str | None,
    ontology_id: str,
) -> ExtractLog:
    return ExtractLog(
        severity="ERROR",
        field_id=field_id,
        message=f"Value ('{concept_id}', '{meaning}') is ignored: scheme '{scheme}' "
        f"does not match expected ontology '{ontology_id}'.",
    )


def invalid_duration_log(field_id: str, value: tuple[str, str]) -> ExtractLog:
    return ExtractLog(
        severity="ERROR",
        field_id=field_id,
        message=f"Value {value} is ignored: not a valid ISO-8601 duration.",
    )


def repeated_value_log(field_id: str, ignored: Sequence[object]) -> ExtractLog:
    """More values than a field holds. The first given is used and the rest ignored."""
    return ExtractLog(
        severity="ERROR",
        field_id=field_id,
        message=f"Values {', '.join(repr(value) for value in ignored)} are ignored: "
        f"the field holds one value and the first one is used.",
    )
