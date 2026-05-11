"""Bigpicture data extraction and loading."""

import re
from pathlib import Path
from typing import Iterator, Literal, cast
from lxml.etree import _ElementTree as ElementTree  # noqa

import fsspec  # type: ignore

from search_api.bigpicture.models import (
    BigpictureFields,
    BigpictureCodeAttributeValue,
    BigpictureStainingFields,
    BigpictureSampleBlockFields,
    BigpictureSampleSpecimenFields,
    BigpictureSampleBiologicalBeingFields,
    BigpictureBlockFields,
)
from search_api.services.dir import list_directories
from search_api.services.xml import parse_xml, validate_xml, get_xml_value

DATASET_XML_FILE = "METADATA/dataset.xml"
IMAGE_XML_FILE = "METADATA/image.xml"
SAMPLE_XML_FILE = "METADATA/sample.xml"
STAINING_XML_FILE = "METADATA/staining.xml"

XML_SCHEMA_DIR = Path(__file__).resolve().parent.parent / "schemas" / "bigpicture"

DATASET_XML_SCHEMA_FILE = "BP.dataset.xsd"
IMAGE_XML_SCHEMA_FILE = "BP.image.xsd"
SAMPLE_XML_SCHEMA_FILE = "BP.sample.xsd"
STAINING_XML_SCHEMA_FILE = "BP.staining.xsd"


# TODO(improve): do not process directories that have already been processed


def extract_fields(
    root: str = "/",
    fs: fsspec.AbstractFileSystem | None = None,
    use_aliases: bool = False,
) -> Iterator[BigpictureFields]:
    """
    Extract search fields from Bigpicture XML directories under the root path.

    :param root: Root directory or bucket path.
    :param fs: Optional fsspec filesystem. If None, a local filesystem is used.
    :param use_aliases: Use XML aliases instead of accessions.
    """
    if fs is None:
        # Use local filesystem.
        fs = fsspec.filesystem("file")

    dirs = list_directories(root=root, fs=fs)

    for d in dirs:
        try:
            dataset_file_path = f"{d}/{DATASET_XML_FILE}"
            image_file_path = f"{d}/{IMAGE_XML_FILE}"
            sample_file_path = f"{d}/{SAMPLE_XML_FILE}"
            staining_file_path = f"{d}/{STAINING_XML_FILE}"

            if not fs.exists(dataset_file_path):
                raise ValueError(f"Missing XML file: {DATASET_XML_FILE}")
            if not fs.exists(image_file_path):
                raise ValueError(f"Missing XML file: {IMAGE_XML_FILE}")
            if not fs.exists(sample_file_path):
                raise ValueError(f"Missing XML file: {SAMPLE_XML_FILE}")
            if not fs.exists(staining_file_path):
                raise ValueError(f"Missing XML file: {STAINING_XML_FILE}")

            if use_aliases:
                id_attribute = "alias"
            else:
                id_attribute = "accession"

            # Map other ids to image ids.

            image_ids: list[str] = []
            map_slide_to_image_ids: dict[str, set[str]] = {}
            map_block_to_slide_ids: dict[str, set[str]] = {}
            map_staining_to_slide_ids: dict[str, set[str]] = {}
            map_specimen_to_block_ids: dict[str, set[str]] = {}
            map_case_to_specimen_ids: dict[str, set[str]] = {}
            map_biological_being_to_case_ids: dict[str, set[str]] = {}
            map_biological_being_to_specimen_ids: dict[str, set[str]] = {}

            map_block_id_to_fields: dict[str, BigpictureSampleBlockFields] = {}
            map_specimen_id_to_fields: dict[str, BigpictureSampleSpecimenFields] = {}
            map_biological_being_id_to_fields: dict[
                str, BigpictureSampleBiologicalBeingFields
            ] = {}
            map_staining_id_to_fields: dict[str, list[BigpictureStainingFields]] = {}

            def add_slide_id_mapping(_slide_id: str, _image_id: str) -> None:
                map_slide_to_image_ids.setdefault(_slide_id, set()).add(_image_id)

            def add_block_id_mapping(_block_id: str, _slide_id: str) -> None:
                map_block_to_slide_ids.setdefault(_block_id, set()).add(_slide_id)

            def add_staining_id_mapping(_staining_id: str, _slide_id: str) -> None:
                map_staining_to_slide_ids.setdefault(_staining_id, set()).add(_slide_id)

            def add_specimen_id_mapping(_specimen_id: str, _block_id: str) -> None:
                map_specimen_to_block_ids.setdefault(_specimen_id, set()).add(_block_id)

            def add_case_id_mapping(_case_id: str, _specimen_id: str) -> None:
                map_case_to_specimen_ids.setdefault(_case_id, set()).add(_specimen_id)

            def add_biological_being_id_to_specimen_mapping(
                _biological_being_id: str, _specimen_id: str
            ):
                map_biological_being_to_specimen_ids.setdefault(
                    _biological_being_id, set()
                ).add(_specimen_id)

            def add_biological_being_id_to_case_mapping(
                _biological_being_id: str, _case_id: str
            ):
                map_biological_being_to_case_ids.setdefault(
                    _biological_being_id, set()
                ).add(_case_id)

            # Read dataset XML.
            #

            with fs.open(dataset_file_path, "rb") as f:
                dataset_xml = parse_xml(f.read())
                validate_xml(dataset_xml, XML_SCHEMA_DIR, DATASET_XML_SCHEMA_FILE)
                dataset_id = get_xml_value(
                    f"/DATASET/@{id_attribute} | /DATASET_SET/DATASET/@{id_attribute}",
                    dataset_xml,
                )
                if dataset_id is None:
                    raise ValueError(
                        f"Failed to extract dataset id from {str(dataset_file_path)}"
                    )
                dataset_short_name = get_xml_value(
                    "/DATASET/DESCRIPTION | /DATASET_SET/DATASET/SHORT_NAME",
                    dataset_xml,
                )
                dataset_title = get_xml_value(
                    "/DATASET/DESCRIPTION | /DATASET_SET/DATASET/TITLE",
                    dataset_xml,
                )
                dataset_description = get_xml_value(
                    "/DATASET/DESCRIPTION | /DATASET_SET/DATASET/DESCRIPTION",
                    dataset_xml,
                )

            # Read image XML.
            #

            with fs.open(image_file_path, "rb") as f:
                image_xml = parse_xml(f.read())
                validate_xml(image_xml, XML_SCHEMA_DIR, IMAGE_XML_SCHEMA_FILE)

                for image_xml in image_xml.xpath("/IMAGE | /IMAGE_SET/IMAGE"):
                    image_id = image_xml.get(id_attribute)
                    if image_id is None:
                        raise ValueError(
                            f"Failed to extract image id from {str(image_file_path)}"
                        )
                    image_ids.append(image_id)
                    slide_ids = image_xml.xpath(f"./IMAGE_OF/@{id_attribute}")
                    for slide_id in slide_ids:
                        add_slide_id_mapping(slide_id, image_id)

            # Read sample XML.
            #

            with fs.open(sample_file_path, "rb") as f:
                sample_xml = parse_xml(f.read())
                validate_xml(sample_xml, XML_SCHEMA_DIR, SAMPLE_XML_SCHEMA_FILE)

                for xml in sample_xml.xpath("/SLIDE | /SAMPLE_SET/SLIDE"):
                    slide_id = xml.get(id_attribute)
                    for block_id in xml.xpath(f"./CREATED_FROM_REF/@{id_attribute}"):
                        add_block_id_mapping(block_id, slide_id)
                    for staining_id in xml.xpath(
                        f"./STAINING_INFORMATION_REF/@{id_attribute}"
                    ):
                        add_staining_id_mapping(staining_id, slide_id)

                for xml in sample_xml.xpath("/BLOCK | /SAMPLE_SET/BLOCK"):
                    block_id = xml.get(id_attribute)
                    # Extract fields from XML.
                    map_block_id_to_fields[block_id] = _extract_sample_block_fields(xml)
                    for specimen_id in xml.xpath(f"./SAMPLED_FROM_REF/@{id_attribute}"):
                        add_specimen_id_mapping(specimen_id, block_id)

                for xml in sample_xml.xpath("/SPECIMEN | /SAMPLE_SET/SPECIMEN"):
                    specimen_id = xml.get(id_attribute)
                    # Extract fields from XML.
                    map_specimen_id_to_fields[specimen_id] = (
                        _extract_sample_specimen_fields(xml)
                    )
                    for case_id in xml.xpath(f"./PART_OF_CASE_REF/@{id_attribute}"):
                        add_case_id_mapping(case_id, specimen_id)
                    for biological_being_id in xml.xpath(
                        f"./EXTRACTED_FROM_REF/@{id_attribute}"
                    ):
                        add_biological_being_id_to_specimen_mapping(
                            biological_being_id, specimen_id
                        )

                for xml in sample_xml.xpath("/CASE | /SAMPLE_SET/CASE"):
                    case_id = xml.get(id_attribute)
                    for biological_being_id in xml.xpath(
                        f"./BIOLOGICAL_BEING_REF/@{id_attribute}"
                    ):
                        add_biological_being_id_to_case_mapping(
                            biological_being_id, case_id
                        )

                for xml in sample_xml.xpath(
                    "/BIOLOGICAL_BEING | /SAMPLE_SET/BIOLOGICAL_BEING"
                ):
                    biological_being_id = xml.get(id_attribute)
                    # Extract fields from XML.
                    map_biological_being_id_to_fields[biological_being_id] = (
                        _extract_sample_biological_being_fields(xml)
                    )

            # Read staining XML.
            #

            with fs.open(staining_file_path, "rb") as f:
                staining_xml = parse_xml(f.read())
                validate_xml(staining_xml, XML_SCHEMA_DIR, STAINING_XML_SCHEMA_FILE)

                for xml in staining_xml.xpath("/STAINING | /STAINING_SET/STAINING"):
                    staining_id = xml.get(id_attribute)
                    # Extract fields from XML.
                    map_staining_id_to_fields[staining_id] = _extract_staining_fields(
                        xml
                    )

            # Finished reading XMLs.

            # Add search fields for each image.
            #

            # Add dataset fields.
            fields: dict[str, BigpictureFields] = {}
            dataset_image_cnt = len(image_ids)
            for image_id in image_ids:
                fields[image_id] = BigpictureFields(
                    dataset_id=dataset_id,
                    image_id=image_id,
                    dataset_image_cnt=dataset_image_cnt,
                    dataset_short_name=dataset_short_name,
                    dataset_title=dataset_title,
                    dataset_description=dataset_description,
                )

            # Add block fields.
            for (
                biological_being_id,
                biological_being_fields,
            ) in map_biological_being_id_to_fields.items():
                specimen_ids = set()
                for case_id in map_biological_being_to_case_ids[biological_being_id]:
                    for specimen_id in map_case_to_specimen_ids[case_id]:
                        specimen_ids.add(specimen_id)
                for specimen_id in map_biological_being_to_specimen_ids[
                    biological_being_id
                ]:
                    specimen_ids.add(specimen_id)

                for specimen_id in specimen_ids:
                    for block_id in map_specimen_to_block_ids[specimen_id]:
                        for slide_id in map_block_to_slide_ids[block_id]:
                            for image_id in map_slide_to_image_ids[slide_id]:
                                f = BigpictureBlockFields(
                                    **map_block_id_to_fields[block_id].model_dump(),
                                    **map_specimen_id_to_fields[
                                        specimen_id
                                    ].model_dump(),
                                    **biological_being_fields.model_dump(),
                                )
                                if any(v is not None for v in f.model_dump().values()):
                                    fields[image_id].blocks.add(f)

            # Add staining fields.
            for staining_id, staining_fields_list in map_staining_id_to_fields.items():
                for staining_fields in staining_fields_list:
                    if any(
                        v is not None for v in staining_fields.model_dump().values()
                    ):
                        for slide_id in map_staining_to_slide_ids[staining_id]:
                            for image_id in map_slide_to_image_ids[slide_id]:
                                fields[image_id].stains.add(staining_fields)

            # Return iterator of extracted fields.
            #

            for image_id in image_ids:
                yield fields[image_id]

        except Exception as e:
            # TODO(improve): add error handling
            raise e


def _extract_sample_block_fields(xml: ElementTree) -> BigpictureSampleBlockFields:
    return BigpictureSampleBlockFields(
        block_preparation=_extract_code_attribute_value(xml, "block_preparation")
    )


def _extract_sample_biological_being_fields(
    xml: ElementTree,
) -> BigpictureSampleBiologicalBeingFields:
    return BigpictureSampleBiologicalBeingFields(
        species=_extract_code_attribute_value(xml, "animal_species"),
        sex=cast(
            Literal["Male", "Female", "Not-known", "Other"] | None,
            _extract_string_attribute_value(xml, "sex"),
        ),
    )


def _extract_sample_specimen_fields(xml: ElementTree) -> BigpictureSampleSpecimenFields:
    return BigpictureSampleSpecimenFields(
        anatomical_site=_extract_code_attribute_value(xml, "anatomical_site"),
        fixation_type=_extract_code_attribute_value(xml, "fixation_type"),
        specimen_type=_extract_code_attribute_value(xml, "specimen_type"),
        age_at_extraction=_extract_age_at_extraction_range(xml),
    )


def _extract_staining_fields(xml: ElementTree) -> list[BigpictureStainingFields]:
    for procedure_xml in xml.xpath("PROCEDURE_INFORMATION"):
        # PROCEDURE_INFORMATION and STAIN(S) are mutually exclusive.
        return [
            BigpictureStainingFields(
                staining_method=_extract_string_attribute_value(
                    procedure_xml, "staining_method", is_attributes=False
                ),
                staining_procedure=_extract_code_attribute_value(
                    procedure_xml, "staining_procedure", is_attributes=False
                ),
                staining_procedure_text=_extract_string_attribute_value(
                    procedure_xml, "staining_procedure", is_attributes=False
                ),
            )
        ]

    fields = []

    for stain_xml in xml.xpath("STAIN"):
        staining_target_text = None
        staining_target = _extract_code_attribute_value(
            stain_xml, "staining_target", is_attributes=False
        )
        if staining_target:
            staining_target_text = staining_target.meaning
        else:
            staining_target_text = _extract_string_attribute_value(
                stain_xml, "staining_target", is_attributes=False
            )

        fields.append(
            BigpictureStainingFields(
                staining_method=_extract_string_attribute_value(
                    stain_xml, "staining_method", is_attributes=False
                ),
                staining_procedure=_extract_code_attribute_value(
                    stain_xml, "staining_procedure", is_attributes=False
                ),
                staining_procedure_text=_extract_string_attribute_value(
                    stain_xml, "staining_procedure", is_attributes=False
                ),
                staining_compound=_extract_code_attribute_value(
                    stain_xml, "staining_compound", is_attributes=False
                ),
                staining_compound_text=_extract_string_attribute_value(
                    stain_xml, "staining_compound", is_attributes=False
                ),
                staining_target=staining_target_text,
            )
        )

    return fields


def _extract_code_attribute_value(
    elem: ElementTree, tag: str, *, is_attributes=True
) -> BigpictureCodeAttributeValue | None:
    if is_attributes:
        values = elem.xpath(f"ATTRIBUTES/CODE_ATTRIBUTE[TAG='{tag}']/VALUE")
    else:
        values = elem.xpath(f"CODE_ATTRIBUTE[TAG='{tag}']/VALUE")

    if not values:
        return None
    value = values[0]

    return BigpictureCodeAttributeValue(
        code=value.findtext("CODE"),
        scheme=value.findtext("SCHEME"),
        meaning=value.findtext("MEANING"),
        scheme_version=value.findtext("SCHEME_VERSION"),
    )


def _extract_string_attribute_value(
    elem: ElementTree, tag: str, *, is_attributes=True
) -> str | None:
    if is_attributes:
        values = elem.xpath(f"ATTRIBUTES/STRING_ATTRIBUTE[TAG='{tag}']/VALUE/text()")
    else:
        values = elem.xpath(f"STRING_ATTRIBUTE[TAG='{tag}']/VALUE/text()")

    if not values:
        return None

    return values[0]


def _extract_age_at_extraction_range(elem: ElementTree) -> tuple[int, int] | None:
    def _get_year(period: str) -> int | None:
        if period == "PT0S":
            return None
        match = re.match(r"P(?P<years>\d+)Y", period)
        return int(match.group("years")) if match else None

    nodes = elem.xpath(
        "//ATTRIBUTES/SET_ATTRIBUTE[TAG/text()='age_at_extraction']/VALUE"
    )
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

    start_year = _get_year(start_value[0])
    length_year = _get_year(length_value[0]) or 0

    if not start_year:
        return None

    return start_year, start_year + length_year
