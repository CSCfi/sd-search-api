"""OpenSearch query-clause builders."""

from datetime import timedelta
from typing import Any

import isodate  # type: ignore[import-untyped]

from search_api.exceptions import UserException

# How much of a text query has to match. Up to two terms all of them
# must match, beyond that three quarters must.
_MATCH_MINIMUM_SHOULD_MATCH = "2<75%"

# Durations are indexed as an integer_range of days. Its bounds are 32-bit signed
# integers, and OpenSearch rejects a query with a larger bound.
_MAX_DAYS = 2**31 - 1


def build_match_clause(field_id: str, value: str) -> dict[str, Any]:
    """Build an OpenSearch match clause over an analysed text field.

    :param field_id: The text field's indexed path.
    :param value: The text to match.
    :return: The match clause.
    """
    return {
        "match": {
            field_id: {
                "query": value,
                "minimum_should_match": _MATCH_MINIMUM_SHOULD_MATCH,
            }
        }
    }


def build_term_clause(field_id: str, value: str) -> dict[str, Any]:
    """Build an OpenSearch term clause.

    :param field_id: The field's indexed path.
    :param value: The exact value to match.
    :return: The term clause.
    """
    return {"term": {field_id: value}}


def build_terms_clause(field_id: str, values: list[str]) -> dict[str, Any]:
    """Build an OpenSearch terms clause matching any of the given values.

    :param field_id: The field's indexed path.
    :param values: The values, any one of which matches.
    :return: The terms clause.
    """
    return {"terms": {field_id: values}}


def iso8601_duration_to_days(duration: str) -> int:
    """Convert an ISO-8601 duration string to number of days.

    Uses: 1 year = 365 days, 1 month = 30 days.

    :param duration: An ISO-8601 duration string, e.g. ``"P40Y"``.
    :return: The equivalent number of days.
    """
    d = isodate.parse_duration(duration)
    if isinstance(d, timedelta):
        return d.days
    return int(d.years) * 365 + int(d.months) * 30 + d.tdelta.days


def iso8601_range_to_days(start: str, end: str) -> tuple[int, int]:
    """Convert a range of two ISO-8601 durations to days.

    :param start: The duration the range starts at.
    :param end: The duration the range ends at.
    :raises ValueError: if either is not a duration, the range ends before it starts,
        or it ends after the most days the index holds. The message does not quote
        the value.
    :return: The first and the last day of the range.
    """
    try:
        gte = iso8601_duration_to_days(start)
        lte = iso8601_duration_to_days(end)
    # isodate raises ValueError for an invalid duration. OverflowError means the
    # duration is too long.
    except ValueError, OverflowError:
        raise ValueError("Not an ISO-8601 duration.") from None
    if gte > lte or lte > _MAX_DAYS:
        raise ValueError("Not a valid ISO-8601 duration range.")
    return gte, lte


def build_iso8601_range_clause(field_id: str, value: str) -> dict[str, Any]:
    """Build an OpenSearch range clause from an ISO-8601 duration range.

    :param field_id: The field's indexed path.
    :param value: One ISO-8601 duration, or two separated by ``-`` for a range.
    :raises UserException: if the value is not a duration or a range, the range ends
        before it starts, or it is too long for the index.
    :return: The range clause.
    """
    # A single duration is a range that starts and ends at it.
    parts = value.split("-", 1)
    try:
        gte, lte = iso8601_range_to_days(parts[0], parts[-1])
    except ValueError:
        raise UserException(
            f"{field_id} value is not an ISO-8601 duration or range, such as P40Y or "
            "P40Y-P60Y."
        ) from None
    return {"range": {field_id: {"gte": gte, "lte": lte}}}


def build_or_clause(clauses: list[dict[str, Any]]) -> dict[str, Any]:
    """Build an OpenSearch bool clause matching any one of the given clauses.

    :param clauses: The clauses, any one of which matches.
    :return: The bool/should clause.
    """
    return {"bool": {"should": clauses, "minimum_should_match": 1}}
