import re
from collections.abc import Iterable
from typing import Any

import isodate  # type: ignore[import-untyped]
from lxml.etree import _Element as Element  # noqa

from search_api.api.beacon.models import SNOMED_ONTOLOGY_ID
from search_api.api.bigpicture.extract.models import BigpictureCodeAttributeValue
from search_api.services.ontology.send import SEND_ONTOLOGY_ID
from search_api.api.extract_logs import (
    ExtractLog,
    invalid_duration_log,
    invalid_scheme_log,
)


_UNCODED_SCHEME = "Other"


_FINDING_XML_TAGS = (
    ("finding", "MISTRESC"),
    ("finding_severity", "MISEV"),
    ("finding_chronicity", "MICHRON"),
    ("finding_distribution", "MIDISTR"),
    ("finding_result_category", "MIRESCAT"),
)


_FINDING_FIELD_IDS = tuple(dict(_FINDING_XML_TAGS))


_XML_TAGS: dict[str, str] = {
    **dict(_FINDING_XML_TAGS),
    "staining_substance": "staining_compound",
}


def _xml_tag(field_id: str) -> str:
    return _XML_TAGS.get(field_id, field_id)


# Supported ontology schema aliases.
_SCHEME_ALIASES: dict[str, frozenset[str]] = {
    SNOMED_ONTOLOGY_ID: frozenset({"snomedct", "snomed", "sct"}),
    SEND_ONTOLOGY_ID: frozenset({"send"}),
}


def _matches_scheme(scheme: str | None, ontology_id: str) -> bool:
    """Match ontology scheme case- and punctuation-insensitively."""
    if scheme is None:
        return False
    normalized = re.sub(r"[^a-z0-9]", "", scheme.lower())
    return normalized in _SCHEME_ALIASES.get(ontology_id, frozenset())


def _filter_value_by_scheme(
    value: BigpictureCodeAttributeValue | None,
    ontology_id: str | None,
    field_id: str,
    logs: list[ExtractLog],
) -> BigpictureCodeAttributeValue | None:
    """Return the code attribute value only if the ontology field has the required scheme."""
    if (
        value is None
        or ontology_id is None
        or _matches_scheme(value.scheme, ontology_id)
    ):
        return value
    logs.append(
        invalid_scheme_log(
            field_id, value.code, value.meaning, value.scheme, ontology_id
        )
    )
    return None


def _filter_values_by_scheme(
    values: Iterable[BigpictureCodeAttributeValue],
    ontology_id: str | None,
    field_id: str,
    logs: list[ExtractLog],
) -> frozenset[BigpictureCodeAttributeValue]:
    """Return the code attribute values only if the ontology field has the required scheme."""
    values = frozenset(values)
    if ontology_id is None:
        return values
    return frozenset(
        required
        for value in values
        if (required := _filter_value_by_scheme(value, ontology_id, field_id, logs))
        is not None
    )


_XSI_NIL = "{http://www.w3.org/2001/XMLSchema-instance}nil"


def _is_nil(elem: Any) -> bool:
    return elem.get(_XSI_NIL) == "true"


def _code_attribute_value(value: Element) -> BigpictureCodeAttributeValue:
    return BigpictureCodeAttributeValue(
        code=value.findtext("CODE"),
        scheme=value.findtext("SCHEME"),
        meaning=value.findtext("MEANING"),
        scheme_version=value.findtext("SCHEME_VERSION"),
    )


def _extract_code_attribute_value(
    elem: Element,
    field_id: str,
    ontology_id: str | None,
    logs: list[ExtractLog],
    *,
    is_attributes: bool = True,
) -> BigpictureCodeAttributeValue | None:
    """Extract CODE_ATTRIBUTE value, requiring its scheme to match the provided ontology."""
    xml_tag = _xml_tag(field_id)
    if is_attributes:
        values = elem.xpath(f"ATTRIBUTES/CODE_ATTRIBUTE[TAG='{xml_tag}']/VALUE")
    else:
        values = elem.xpath(f"CODE_ATTRIBUTE[TAG='{xml_tag}']/VALUE")

    if not values or _is_nil(values[0]):
        return None

    return _filter_value_by_scheme(
        _code_attribute_value(values[0]), ontology_id, field_id, logs
    )


def _extract_code_attribute_values(
    elem: Element,
    field_id: str,
    ontology_id: str | None,
    logs: list[ExtractLog],
    *,
    is_attributes: bool = True,
) -> frozenset[BigpictureCodeAttributeValue]:
    """Extract CODE_ATTRIBUTE values, requiring their scheme to match the provided ontology."""
    xml_tag = _xml_tag(field_id)
    if is_attributes:
        values = elem.xpath(f"ATTRIBUTES/CODE_ATTRIBUTE[TAG='{xml_tag}']/VALUE")
    else:
        values = elem.xpath(f"CODE_ATTRIBUTE[TAG='{xml_tag}']/VALUE")

    codes = (_code_attribute_value(v) for v in values if not _is_nil(v))
    return _filter_values_by_scheme(codes, ontology_id, field_id, logs)


def _extract_string_attribute_value(
    elem: Element, tag: str, *, is_attributes=True
) -> str | None:
    if is_attributes:
        values = elem.xpath(f"ATTRIBUTES/STRING_ATTRIBUTE[TAG='{tag}']/VALUE/text()")
    else:
        values = elem.xpath(f"STRING_ATTRIBUTE[TAG='{tag}']/VALUE/text()")

    if not values:
        return None

    return values[0]


def _extract_anatomical_sites(
    elem: Element, logs: list[ExtractLog]
) -> frozenset[BigpictureCodeAttributeValue]:
    direct = _extract_code_attribute_values(
        elem, "anatomical_site", SNOMED_ONTOLOGY_ID, logs
    )

    set_nodes = elem.xpath("ATTRIBUTES/SET_ATTRIBUTE[TAG='anatomical_site_list']/VALUE")
    from_set: frozenset[BigpictureCodeAttributeValue] = frozenset()
    if set_nodes:
        from_set = _extract_code_attribute_values(
            set_nodes[0],
            "anatomical_site",
            SNOMED_ONTOLOGY_ID,
            logs,
            is_attributes=False,
        )

    return direct | from_set


def _extract_fixation_type(
    xml: Element, logs: list[ExtractLog]
) -> tuple[BigpictureCodeAttributeValue | None, str | None]:
    # If schema is "Other" then no ontology is used. Otherwise, require Snomed.
    value = _extract_code_attribute_value(xml, "fixation_type", None, logs)

    if value and value.scheme == _UNCODED_SCHEME:
        return None, value.meaning or value.code

    return _filter_value_by_scheme(
        value, SNOMED_ONTOLOGY_ID, "fixation_type", logs
    ), None


def _iso8601_duration(start: str, length: str) -> str:
    result = isodate.parse_duration(start) + isodate.parse_duration(length)

    if isinstance(result, isodate.Duration):
        # isodate does not normalise month overflow; do it explicitly.
        extra_years, months = divmod(int(result.months), 12)
        years = int(result.years) + extra_years
        result = isodate.Duration(years=years, months=months) + result.tdelta

    return isodate.duration_isoformat(result)


def _extract_age_at_extraction(
    elem: Element, logs: list[ExtractLog]
) -> tuple[str, str] | None:
    nodes = elem.xpath("ATTRIBUTES/SET_ATTRIBUTE[TAG/text()='age_at_extraction']/VALUE")
    if not nodes:
        return None

    node = nodes[0]
    start_value = node.xpath(
        "STRING_ATTRIBUTE[TAG/text()='interval_start']/VALUE/text()"
    )
    length_value = node.xpath(
        "STRING_ATTRIBUTE[TAG/text()='interval_length']/VALUE/text()"
    )
    if not start_value or not length_value:
        return None

    start = start_value[0]
    try:
        end = _iso8601_duration(start, length_value[0])
    except isodate.ISO8601Error:
        logs.append(invalid_duration_log("age_at_extraction", (start, length_value[0])))
        return None

    return start, end
