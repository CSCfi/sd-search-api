import re
from collections.abc import Iterable
from typing import Any, Literal, cast

import isodate  # type: ignore[import-untyped]
from lxml.etree import _Element as Element, _ElementTree as ElementTree  # noqa

from search_api.api.beacon.models import SNOMED_ONTOLOGY_ID
from search_api.api.bigpicture.extract.models import (
    BigpictureCodeAttributeValue,
    BigpictureFindingFields,
    BigpictureSampleBiologicalBeingFields,
    BigpictureSampleBlockFields,
    BigpictureSampleSpecimenFields,
    BigpictureStainingFields,
    BigpictureExtractedObject,
)
from search_api.api.bigpicture.extract.refs import object_ids
from search_api.exceptions import UserException
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


_XSI_NIL = "{http://www.w3.org/2001/XMLSchema-instance}nil"


def _is_nil(elem: Any) -> bool:
    return elem.get(_XSI_NIL) == "true"


# Filter ontology values by scheme.
#


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


# Extract code attribute value.
#


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


# Extract string attribute value.
#


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


# Extract code or string attribute value.
#


def _extract_ontology_or_value(
    elem: Element,
    field_id: str,
    ontology_id: str,
    logs: list[ExtractLog],
    *,
    is_attributes: bool = True,
) -> tuple[BigpictureCodeAttributeValue | None, str | None]:
    """Return an ontologyOrValue field's coded value or its free text alternative.

    At most one of the two is returned. A coded value takes precedence.
    """
    value = _extract_code_attribute_value(
        elem, field_id, ontology_id, logs, is_attributes=is_attributes
    )
    if value is not None:
        return value, None
    return None, _extract_string_attribute_value(
        elem, _xml_tag(field_id), is_attributes=is_attributes
    )


# Extract ISO 8601 duration.
#


def _extract_iso8601_duration(start: str, length: str) -> str:
    result = isodate.parse_duration(start) + isodate.parse_duration(length)

    if isinstance(result, isodate.Duration):
        # isodate does not normalise month overflow; do it explicitly.
        extra_years, months = divmod(int(result.months), 12)
        years = int(result.years) + extra_years
        result = isodate.Duration(years=years, months=months) + result.tdelta

    return isodate.duration_isoformat(result)


# Extract specific fields.
#


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
        end = _extract_iso8601_duration(start, length_value[0])
    except isodate.ISO8601Error:
        logs.append(invalid_duration_log("age_at_extraction", (start, length_value[0])))
        return None

    return start, end


def extract_sample_block_fields(
    xml: Element,
) -> BigpictureExtractedObject[BigpictureSampleBlockFields]:
    logs: list[ExtractLog] = []
    return BigpictureExtractedObject(
        ids=object_ids(xml),
        fields=BigpictureSampleBlockFields(
            block_preparation=_extract_code_attribute_value(
                xml, "block_preparation", SNOMED_ONTOLOGY_ID, logs
            )
        ),
        logs=logs,
    )


def extract_sample_biological_being_fields(
    xml: Element,
) -> BigpictureExtractedObject[BigpictureSampleBiologicalBeingFields]:
    logs: list[ExtractLog] = []
    return BigpictureExtractedObject(
        ids=object_ids(xml),
        fields=BigpictureSampleBiologicalBeingFields(
            animal_species=_extract_code_attribute_value(
                xml, "animal_species", SNOMED_ONTOLOGY_ID, logs
            ),
            sex=cast(
                Literal["Male", "Female", "Not-known", "Other"] | None,
                _extract_string_attribute_value(xml, "sex"),
            ),
        ),
        logs=logs,
    )


def extract_sample_specimen_fields(
    xml: Element,
) -> BigpictureExtractedObject[BigpictureSampleSpecimenFields]:
    logs: list[ExtractLog] = []
    fixation_type, fixation_type_text = _extract_fixation_type(xml, logs)

    return BigpictureExtractedObject(
        ids=object_ids(xml),
        fields=BigpictureSampleSpecimenFields(
            anatomical_site=_extract_anatomical_sites(xml, logs),
            fixation_type=fixation_type,
            fixation_type_other=fixation_type_text,
            specimen_type=_extract_code_attribute_value(
                xml, "specimen_type", SNOMED_ONTOLOGY_ID, logs
            ),
            age_at_extraction=_extract_age_at_extraction(xml, logs),
        ),
        logs=logs,
    )


def extract_staining_fields(
    xml: Element,
) -> BigpictureExtractedObject[list[BigpictureStainingFields]]:
    logs: list[ExtractLog] = []
    for procedure_xml in xml.xpath("PROCEDURE_INFORMATION"):
        # PROCEDURE_INFORMATION and STAIN(S) are mutually exclusive.
        procedure, procedure_other = _extract_ontology_or_value(
            procedure_xml,
            "staining_procedure",
            SNOMED_ONTOLOGY_ID,
            logs,
            is_attributes=False,
        )
        return BigpictureExtractedObject(
            ids=object_ids(xml),
            fields=[
                BigpictureStainingFields(
                    staining_procedure=procedure,
                    staining_procedure_other=procedure_other,
                )
            ],
            logs=logs,
        )

    fields = []

    for stain_xml in xml.xpath("STAIN"):
        staining_method = _extract_string_attribute_value(
            stain_xml, "staining_method", is_attributes=False
        )
        is_chemical_stain = staining_method == "chemical"
        staining_target_text = None
        if not is_chemical_stain:
            # staining_target is stored as free text regardless of ontology.
            staining_target = _extract_code_attribute_value(
                stain_xml, "staining_target", None, logs, is_attributes=False
            )
            if staining_target:
                staining_target_text = staining_target.meaning
            else:
                staining_target_text = _extract_string_attribute_value(
                    stain_xml, "staining_target", is_attributes=False
                )

        procedure, procedure_other = _extract_ontology_or_value(
            stain_xml,
            "staining_procedure",
            SNOMED_ONTOLOGY_ID,
            logs,
            is_attributes=False,
        )
        substance, substance_other = (
            _extract_ontology_or_value(
                stain_xml,
                "staining_substance",
                SNOMED_ONTOLOGY_ID,
                logs,
                is_attributes=False,
            )
            if is_chemical_stain
            else (None, None)
        )

        fields.append(
            BigpictureStainingFields(
                staining_procedure=procedure,
                staining_procedure_other=procedure_other,
                staining_substance=substance,
                staining_substance_other=substance_other,
                staining_target=staining_target_text,
            )
        )

    return BigpictureExtractedObject(ids=object_ids(xml), fields=fields, logs=logs)


def extract_diagnoses(
    statement: Element, logs: list[ExtractLog]
) -> set[BigpictureCodeAttributeValue]:
    codes = {
        _code_attribute_value(v)
        for v in statement.xpath("CODE_ATTRIBUTES/CODE_ATTRIBUTE/VALUE")
        if not _is_nil(v)
    }
    return set(_filter_values_by_scheme(codes, SNOMED_ONTOLOGY_ID, "diagnosis", logs))


def extract_finding(
    statement: Element,
    logs: list[ExtractLog],
    qualifiers: frozenset[tuple[str, str]],
) -> BigpictureFindingFields | None:
    """Build one finding from a ``Finding`` statement, or None if it holds none.

    If a tag repeats within a statement the first value is used.
    """
    code_attributes = statement.xpath("CODE_ATTRIBUTES")
    if not code_attributes:
        return None
    values = {
        field_id: _extract_code_attribute_value(
            code_attributes[0], field_id, SEND_ONTOLOGY_ID, logs, is_attributes=False
        )
        for field_id in _FINDING_FIELD_IDS
    }
    if not any(value is not None for value in values.values()):
        return None
    return BigpictureFindingFields(**values, qualifiers=qualifiers)


# Extract scope.
#


_TYPE_OF_DATASET_TAG = "type_of_dataset"


# Scope by lower-cased part of 'type_of_dataset' string attribute before the "/".
_SCOPE_BY_DATASET_TYPE: dict[str, Literal["clinical", "non_clinical"]] = {
    "clinical": "clinical",
    "non-clinical": "non_clinical",
}


def extract_scope(
    policy_xml: ElementTree, policy_file_path: str
) -> Literal["clinical", "non_clinical"]:
    """Extract dataset scope from policy ``type_of_dataset`` attribute.

    The scope before the ``"/"`` is read case-insensitively.

    :raises UserException: if the attribute is missing or its scope is unknown.
    """
    for policy in policy_xml.xpath("/POLICY | /POLICY_SET/POLICY"):
        value = _extract_string_attribute_value(policy, _TYPE_OF_DATASET_TAG)
        if value is None:
            continue
        scope = _SCOPE_BY_DATASET_TYPE.get(value.partition("/")[0].strip().lower())
        if scope is None:
            raise UserException(
                f"Unsupported '{_TYPE_OF_DATASET_TAG}' value {value!r} in {policy_file_path}."
            )
        return scope
    raise UserException(
        f"Missing '{_TYPE_OF_DATASET_TAG}' attribute in {policy_file_path}."
    )
