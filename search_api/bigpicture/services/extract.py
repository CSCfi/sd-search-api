"""Bigpicture XML extraction service."""

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Literal, cast
from lxml.etree import _ElementTree as ElementTree  # noqa

import fsspec  # type: ignore
import isodate  # type: ignore[import-untyped]
from pydantic import BaseModel, ConfigDict, Field, field_serializer

from search_api.api.bigpicture.models import BP_DOCUMENT_FIELDS
from search_api.exceptions import SystemException
from search_api.api.opensearch.models import ExtractedDocument, OpenSearchFieldValue
from search_api.services.crypt import load_c4gh_keys, read_file, resolve_path
from search_api.services.dir import list_directories
from search_api.services.xml import parse_xml, validate_xml, get_xml_value


# Parsing models for Bigpicture XML, converted to OpenSearchFieldValues by to_opensearch_field_values.


class BigpictureCodeAttributeValue(BaseModel):
    """Bigpicture code attribute value."""

    model_config = ConfigDict(frozen=True)

    code: str
    scheme: str | None = None
    meaning: str
    scheme_version: str | None = None


class BigpictureSampleBiologicalBeingFields(BaseModel):
    """Bigpicture biological being search fields."""

    animal_species: BigpictureCodeAttributeValue | None = None
    sex: Literal["Male", "Female", "Not-known", "Other"] | None = None


class BigpictureSampleSpecimenFields(BaseModel):
    """Bigpicture specimen search fields."""

    anatomical_site: frozenset[BigpictureCodeAttributeValue] = Field(
        default_factory=frozenset
    )
    fixation_type: BigpictureCodeAttributeValue | None = None
    fixation_type_other: str | None = None  # Free text alternative
    specimen_type: BigpictureCodeAttributeValue | None = None
    age_at_extraction: tuple[str, str] | None = None

    @field_serializer("anatomical_site")
    def _serialize_anatomical_site(
        self, v: frozenset[BigpictureCodeAttributeValue]
    ) -> list[dict]:
        # Set elements must be hashable; Pydantic serialises frozenset[BaseModel] as
        # set[dict], but dict is unhashable, so serialise as list[dict].
        return [item.model_dump() for item in v]


class BigpictureSampleBlockFields(BaseModel):
    """Bigpicture block search fields."""

    block_preparation: BigpictureCodeAttributeValue | None = None


class BigpictureSpecimenFields(
    BigpictureSampleBiologicalBeingFields,
    BigpictureSampleSpecimenFields,
    BigpictureSampleBlockFields,
    BaseModel,
):
    """Bigpicture specimen search fields (see grouping rationale in fields.yaml)."""

    model_config = ConfigDict(frozen=True)


class BigpictureStainingFields(BaseModel):
    """Bigpicture staining search field."""

    model_config = ConfigDict(frozen=True)

    staining_procedure: BigpictureCodeAttributeValue | None = None
    staining_procedure_other: str | None = None  # Free text alternative
    staining_substance: BigpictureCodeAttributeValue | None = None
    staining_substance_other: str | None = None  # Free text alternative
    staining_target: str | None = None


class BigpictureFields(BaseModel):
    """Bigpicture IDs and search fields."""

    image_id: str
    dataset_id: str
    dataset_image_cnt: int
    dataset_short_name: str | None = None
    dataset_title: str | None = None
    dataset_description: str | None = None
    # Newest file modification date in the dataset.
    dataset_modified_at: datetime | None = None
    specimens: set[BigpictureSpecimenFields] = Field(default_factory=set)
    stainings: set[BigpictureStainingFields] = Field(default_factory=set)


def _has_value(value: Any) -> bool:
    """Whether a parsed field carries an indexable value."""
    if value is None:
        return False
    if isinstance(value, frozenset):
        return len(value) > 0
    return True


def to_opensearch_field_values(fields: BigpictureFields) -> list[OpenSearchFieldValue]:
    """Convert extracted field models to OpenSearch field values."""
    values: list[OpenSearchFieldValue] = []

    def add_value(index: int, field_name: str, value: Any) -> None:
        if not _has_value(value):
            return
        field = BP_DOCUMENT_FIELDS.get(field_name)
        if field is None:
            raise SystemException(
                f"Field {field_name!r} is not registered in BP_DOCUMENT_FIELDS"
            )
        if isinstance(value, BigpictureCodeAttributeValue):
            values.append(
                OpenSearchFieldValue(field=field, value=value.code, index=index)
            )
        elif isinstance(value, frozenset):
            for item in value:
                values.append(
                    OpenSearchFieldValue(field=field, value=item.code, index=index)
                )
        elif isinstance(value, (tuple, int, str)):
            values.append(OpenSearchFieldValue(field=field, value=value, index=index))
        else:
            raise SystemException(
                f"Field {field_name!r} has an unexpected value type: {type(value).__name__!r}"
            )

    # Add root level fields.
    for field_name in type(fields).model_fields:
        if field_name in BP_DOCUMENT_FIELDS:
            add_value(0, field_name, getattr(fields, field_name))

    # Add nested fields.
    for items in (fields.specimens, fields.stainings):
        index = 0
        for item in items:
            before = len(values)
            for field_name in type(item).model_fields:
                add_value(index, field_name, getattr(item, field_name))
            if len(values) > before:
                # Index advances if a new value was added.
                index += 1

    return values


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


def extract_documents(
    root: str = "/",
    fs: fsspec.AbstractFileSystem | None = None,
    use_aliases: bool = False,
    single_dir: bool = False,
    c4gh_private_key_file: str | None = None,
    c4gh_passphrase: str | None = None,
) -> Iterator[ExtractedDocument]:
    """
    Extract search fields from Bigpicture XML directories under the root path.

    :param root: Root directory or bucket path.
    :param fs: Optional fsspec filesystem. If None, a local filesystem is used.
    :param use_aliases: Use XML aliases instead of accessions.
    :param single_dir: If True, treat root as a single dataset directory instead of
        a parent directory containing multiple dataset directories.
    :param c4gh_private_key_file: Path to a Crypt4GH private key file (.sec) for
        decrypting ``.c4gh`` files. If None, only plain files are accepted.
    :param c4gh_passphrase: Passphrase protecting the private key, or None for an
        unprotected key.
    """
    if fs is None:
        # Use local filesystem.
        fs = fsspec.filesystem("file")

    keys = (
        load_c4gh_keys(c4gh_private_key_file, c4gh_passphrase)
        if c4gh_private_key_file
        else None
    )

    dirs = [root] if single_dir else list_directories(root=root, fs=fs)

    for d in dirs:
        try:
            dataset_file_path = resolve_path(fs, f"{d}/{DATASET_XML_FILE}")
            image_file_path = resolve_path(fs, f"{d}/{IMAGE_XML_FILE}")
            sample_file_path = resolve_path(fs, f"{d}/{SAMPLE_XML_FILE}")
            staining_file_path = resolve_path(fs, f"{d}/{STAINING_XML_FILE}")

            dataset_modified_at = _get_last_modification_time(
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

            def add_biological_being_id_to_specimen_mapping(
                _biological_being_id: str, _specimen_id: str
            ):
                map_biological_being_to_specimen_ids.setdefault(
                    _biological_being_id, set()
                ).add(_specimen_id)

            # Read dataset XML.
            #

            dataset_xml = parse_xml(read_file(fs, dataset_file_path, keys))
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

            image_xml = parse_xml(read_file(fs, image_file_path, keys))
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

            sample_xml = parse_xml(read_file(fs, sample_file_path, keys))
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
                for biological_being_id in xml.xpath(
                    f"./EXTRACTED_FROM_REF/@{id_attribute}"
                ):
                    add_biological_being_id_to_specimen_mapping(
                        biological_being_id, specimen_id
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

            staining_xml = parse_xml(read_file(fs, staining_file_path, keys))
            validate_xml(staining_xml, XML_SCHEMA_DIR, STAINING_XML_SCHEMA_FILE)

            for xml in staining_xml.xpath("/STAINING | /STAINING_SET/STAINING"):
                staining_id = xml.get(id_attribute)
                # Extract fields from XML.
                map_staining_id_to_fields[staining_id] = _extract_staining_fields(xml)

            # Finished reading XMLs.

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
                    dataset_modified_at=dataset_modified_at,
                )

            # Add specimen fields. A specimen is extracted from exactly one
            # biological being, and its block has a single field, so both are
            # flattened into the specimen.
            for (
                biological_being_id,
                biological_being_fields,
            ) in map_biological_being_id_to_fields.items():
                for specimen_id in map_biological_being_to_specimen_ids[
                    biological_being_id
                ]:
                    for block_id in map_specimen_to_block_ids[specimen_id]:
                        for slide_id in map_block_to_slide_ids[block_id]:
                            for image_id in map_slide_to_image_ids[slide_id]:
                                specimen = BigpictureSpecimenFields(
                                    **map_block_id_to_fields[block_id].model_dump(),
                                    **map_specimen_id_to_fields[
                                        specimen_id
                                    ].model_dump(),
                                    **biological_being_fields.model_dump(),
                                )
                                fields[image_id].specimens.add(specimen)

            # Add staining fields.
            for staining_id, staining_fields_list in map_staining_id_to_fields.items():
                for staining_fields in staining_fields_list:
                    if any(
                        v is not None for v in staining_fields.model_dump().values()
                    ):
                        for slide_id in map_staining_to_slide_ids[staining_id]:
                            for image_id in map_slide_to_image_ids[slide_id]:
                                fields[image_id].stainings.add(staining_fields)

            # Return iterator of extracted documents.
            for image_id in image_ids:
                bp_fields = fields[image_id]
                yield ExtractedDocument(
                    id=bp_fields.image_id,
                    modified_at=bp_fields.dataset_modified_at,
                    values=to_opensearch_field_values(bp_fields),
                )

        except Exception:
            logging.error("Failed to extract fields from dataset %s.", d, exc_info=True)
            raise


def _extract_sample_block_fields(xml: ElementTree) -> BigpictureSampleBlockFields:
    return BigpictureSampleBlockFields(
        block_preparation=_extract_code_attribute_value(xml, "block_preparation")
    )


def _extract_sample_biological_being_fields(
    xml: ElementTree,
) -> BigpictureSampleBiologicalBeingFields:
    return BigpictureSampleBiologicalBeingFields(
        animal_species=_extract_code_attribute_value(xml, "animal_species"),
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
        fixation_type_other=fixation_type_text,
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
                staining_procedure_other=_extract_string_attribute_value(
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
                staining_procedure_other=_extract_string_attribute_value(
                    stain_xml, "staining_procedure", is_attributes=False
                ),
                staining_substance=_extract_code_attribute_value(
                    stain_xml, "staining_compound", is_attributes=False
                )
                if is_chemical_stain
                else None,
                staining_substance_other=_extract_string_attribute_value(
                    stain_xml, "staining_compound", is_attributes=False
                )
                if is_chemical_stain
                else None,
                staining_target=staining_target_text,
            )
        )

    return fields


_XSI_NIL = "{http://www.w3.org/2001/XMLSchema-instance}nil"


def _is_nil(elem: Any) -> bool:
    return elem.get(_XSI_NIL) == "true"


def _extract_code_attribute_value(
    elem: ElementTree, tag: str, *, is_attributes=True
) -> BigpictureCodeAttributeValue | None:
    if is_attributes:
        values = elem.xpath(f"ATTRIBUTES/CODE_ATTRIBUTE[TAG='{tag}']/VALUE")
    else:
        values = elem.xpath(f"CODE_ATTRIBUTE[TAG='{tag}']/VALUE")

    if not values or _is_nil(values[0]):
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
        if not _is_nil(v)
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
