from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict

from search_api.severity import LogSeverity


class ExtractLog(BaseModel):
    """One problem an extraction found.

    The message must never contain source document values. This can be sensitive
    information that should not be logged, stored or reported. A concept id, term or
    controlled value confirmed to exist is not sensitive alone, so a message may
    quote one. It must not quote several, since together they may identify a person.
    """

    model_config = ConfigDict(frozen=True)

    severity: LogSeverity
    message: str
    field_id: str | None = None


def invalid_scheme_log(field_id: str, ontology_id: str) -> ExtractLog:
    """Log a code attribute whose scheme is not the field's ontology.

    The code, meaning and scheme are not quoted. They are submitter text that is
    dropped before being looked up in any ontology, so they may be free text.
    """
    return ExtractLog(
        severity="ERROR",
        field_id=field_id,
        message=f"Value is ignored: its scheme does not match expected ontology "
        f"'{ontology_id}'.",
    )


def invalid_duration_log(field_id: str) -> ExtractLog:
    return ExtractLog(
        severity="ERROR",
        field_id=field_id,
        message="Value is ignored: not a valid ISO-8601 duration.",
    )


def not_allowed_value_log(field_id: str, allowed: Sequence[str]) -> ExtractLog:
    allowed_values = ", ".join(f"'{allowed_value}'" for allowed_value in allowed)
    return ExtractLog(
        severity="ERROR",
        field_id=field_id,
        message=f"Value is ignored: not one of {allowed_values}.",
    )


def repeated_value_log(field_id: str, ignored_count: int) -> ExtractLog:
    """Log a field that takes one value but was given several.

    The first value is used and the others are ignored.

    :param ignored_count: The number of values ignored after the first.
    """
    return ExtractLog(
        severity="ERROR",
        field_id=field_id,
        message=f"The field takes one value but {ignored_count + 1} were given. "
        f"Only the first one is used.",
    )
