"""Bigpicture XML extraction service."""

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Literal, cast
from lxml.etree import _ElementTree as ElementTree  # noqa

import fsspec  # type: ignore
import isodate  # type: ignore[import-untyped]

from search_api.bigpicture.services.load import BigPictureLoadService
from search_api.database.repository import get_cursor
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

XML_SCHEMA_DIR = (
    Path(__file__).resolve().parent.parent.parent / "schemas" / "bigpicture"
)

DATASET_XML_SCHEMA_FILE = "BP.dataset.xsd"
IMAGE_XML_SCHEMA_FILE = "BP.image.xsd"
SAMPLE_XML_SCHEMA_FILE = "BP.sample.xsd"
STAINING_XML_SCHEMA_FILE = "BP.staining.xsd"


def _get_last_modification_time(
    fs: fsspec.AbstractFileSystem, file_paths: list[str]
) -> datetime | None:
    """Return the last modification time across the given file paths, or None on error."""
    mtimes: list[datetime] = []
    for path in file_paths:
        try:
            info = fs.info(path)
            mtime = (
                info.get("mtime")  # local filesystem (fsspec LocalFileSystem)
                or info.get(
                    "last_modified"
                )  # GCS, HTTP (fsspec GCSFileSystem, HTTPFileSystem)
                or info.get("LastModified")  # S3 raw boto3 key (fsspec S3FileSystem)
            )
            if mtime is None:
                continue
            if isinstance(mtime, (int, float)):
                mtimes.append(datetime.fromtimestamp(mtime, tz=timezone.utc))
            elif isinstance(mtime, datetime):
                mtimes.append(
                    mtime if mtime.tzinfo else mtime.replace(tzinfo=timezone.utc)
                )
        except Exception:
            logging.warning("Could not extract last modification time for %s.", path)
    return max(mtimes) if mtimes else None


def extract_fields(
    root: str = "/",
    fs: fsspec.AbstractFileSystem | None = None,
    use_aliases: bool = False,
    single_dir: bool = False,
) -> Iterator[BigpictureFields]:
    """
    Extract search fields from Bigpicture XML directories under the root path.

    :param root: Root directory or bucket path.
    :param fs: Optional fsspec filesystem. If None, a local filesystem is used.
    :param use_aliases: Use XML aliases instead of accessions.
    :param single_dir: If True, treat root as a single dataset directory instead of
        a parent directory containing multiple dataset directories.
    """
    if fs is None:
        # Use local filesystem.
        fs = fsspec.filesystem("file")

    dirs = [root] if single_dir else list_directories(root=root, fs=fs)

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

            dataset_files_date = _get_last_modification_time(
                fs,
                [
                    dataset_file_path,
                    image_file_path,
                    sample_file_path,
                    staining_file_path,
                ],
            )

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
                    dataset_files_date=dataset_files_date,
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
    fixation_type, fixation_type_text = _extract_fixation_type(xml)

    return BigpictureSampleSpecimenFields(
        anatomical_site=_extract_anatomical_sites(xml),
        fixation_type=fixation_type,
        fixation_type_text=fixation_type_text,
        specimen_type=_extract_code_attribute_value(xml, "specimen_type"),
        age_at_extraction=_extract_age_at_extraction_range(xml),
    )


def _extract_staining_fields(xml: ElementTree) -> list[BigpictureStainingFields]:
    for procedure_xml in xml.xpath("PROCEDURE_INFORMATION"):
        # PROCEDURE_INFORMATION and STAIN(S) are mutually exclusive.
        return [
            BigpictureStainingFields(
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
        staining_method = _extract_string_attribute_value(
            stain_xml, "staining_method", is_attributes=False
        )
        is_chemical_stain = staining_method == "chemical"
        staining_target_text = None
        if not is_chemical_stain:
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
                staining_procedure=_extract_code_attribute_value(
                    stain_xml, "staining_procedure", is_attributes=False
                ),
                staining_procedure_text=_extract_string_attribute_value(
                    stain_xml, "staining_procedure", is_attributes=False
                ),
                staining_substance=_extract_code_attribute_value(
                    stain_xml, "staining_compound", is_attributes=False
                )
                if is_chemical_stain
                else None,
                staining_substance_text=_extract_string_attribute_value(
                    stain_xml, "staining_compound", is_attributes=False
                )
                if is_chemical_stain
                else None,
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


def _extract_code_attribute_values(
    elem: ElementTree, tag: str, *, is_attributes=True
) -> frozenset[BigpictureCodeAttributeValue]:
    if is_attributes:
        values = elem.xpath(f"ATTRIBUTES/CODE_ATTRIBUTE[TAG='{tag}']/VALUE")
    else:
        values = elem.xpath(f"CODE_ATTRIBUTE[TAG='{tag}']/VALUE")

    return frozenset(
        BigpictureCodeAttributeValue(
            code=v.findtext("CODE"),
            scheme=v.findtext("SCHEME"),
            meaning=v.findtext("MEANING"),
            scheme_version=v.findtext("SCHEME_VERSION"),
        )
        for v in values
    )


def _extract_anatomical_sites(
    elem: ElementTree,
) -> frozenset[BigpictureCodeAttributeValue]:
    direct = _extract_code_attribute_values(elem, "anatomical_site")

    set_nodes = elem.xpath("ATTRIBUTES/SET_ATTRIBUTE[TAG='anatomical_site_list']/VALUE")
    from_set: frozenset[BigpictureCodeAttributeValue] = frozenset()
    if set_nodes:
        from_set = _extract_code_attribute_values(
            set_nodes[0], "anatomical_site", is_attributes=False
        )

    return direct | from_set


def _extract_fixation_type(
    xml: ElementTree,
) -> tuple[BigpictureCodeAttributeValue | None, str | None]:
    value = _extract_code_attribute_value(xml, "fixation_type")

    if value and value.scheme == "Other":
        return None, value.meaning or value.code

    return value, None


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


def _add_iso8601_durations(start: str, length: str) -> str:
    """Add an ISO-8601 duration ``length`` to ``start`` and return the ISO-8601 result."""

    result = isodate.parse_duration(start) + isodate.parse_duration(length)

    if isinstance(result, isodate.Duration):
        # isodate does not normalise month overflow; do it explicitly.
        extra_years, months = divmod(int(result.months), 12)
        years = int(result.years) + extra_years
        result = isodate.Duration(years=years, months=months) + result.tdelta

    return isodate.duration_isoformat(result)


def _extract_age_at_extraction_range(elem: ElementTree) -> tuple[str, str] | None:
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

    start = start_value[0]
    try:
        end = _add_iso8601_durations(start, length_value[0])
    except isodate.ISO8601Error:
        logging.error(
            "Invalid ISO-8601 duration in age_at_extraction (start=%r, length=%r); skipping field.",
            start,
            length_value[0],
        )
        return None

    return start, end


class BigPictureExtractService:
    """Service for extracting Bigpicture fields from XML files."""

    @staticmethod
    def extract_fields(
        root: str = "/",
        fs: fsspec.AbstractFileSystem | None = None,
        use_aliases: bool = False,
        single_dir: bool = False,
    ) -> Iterator[BigpictureFields]:
        """
        Extract search fields from Bigpicture XML directories under the root path.

        :param root: Local directory or bucket path containing dataset subdirectories,
            or a single dataset directory if ``single_dir`` is True.
        :param fs: Optional fsspec filesystem. If None, a local filesystem is used.
        :param use_aliases: Use XML aliases instead of accessions.
        :param single_dir: If True, treat root as a single dataset directory instead of
            a parent directory containing multiple dataset directories.
        """
        return extract_fields(root, fs, use_aliases, single_dir)

    async def extract_and_load_fields(
        self,
        root: str = "/",
        fs: fsspec.AbstractFileSystem | None = None,
        use_aliases: bool = False,
        single_dir: bool = False,
    ) -> None:
        """
        Extract fields from XML files and load them into the database.

        :param root: Local directory or bucket path containing dataset subdirectories,
            or a single dataset directory if ``single_dir`` is True.
        :param fs: Optional fsspec filesystem. If None, a local filesystem is used.
        :param use_aliases: Use XML aliases instead of accessions.
        :param single_dir: If True, treat root as a single dataset directory instead of
            a parent directory containing multiple dataset directories.
        """
        logging.info("Loading fields from %s.", root)
        loaded = 0
        skipped_datasets: set[str] = set()
        checked_datasets: set[str] = set()
        async with get_cursor() as cur:
            for fields in self.extract_fields(root, fs, use_aliases, single_dir):
                if fields.dataset_id in skipped_datasets:
                    continue

                if fields.dataset_id not in checked_datasets:
                    checked_datasets.add(fields.dataset_id)
                    existing_date = await BigPictureLoadService.get_dataset_files_date(
                        cur, fields.dataset_id
                    )
                    if (
                        existing_date is not None
                        and fields.dataset_files_date is not None
                        and existing_date >= fields.dataset_files_date
                    ):
                        logging.info(
                            "Skipping dataset %s — no newer files.", fields.dataset_id
                        )
                        skipped_datasets.add(fields.dataset_id)
                        continue

                await BigPictureLoadService.load_fields(cur, fields)
                loaded += 1
                logging.info(
                    "Loaded image %s (dataset %s).", fields.image_id, fields.dataset_id
                )

        logging.info(
            "Done — loaded %d image(s), skipped %d dataset(s).",
            loaded,
            len(skipped_datasets),
        )
