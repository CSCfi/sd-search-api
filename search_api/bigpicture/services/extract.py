"""Bigpicture XML extraction service."""

import logging
from collections.abc import Iterable, Sequence, Set
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Literal, TypeVar, cast
from lxml.etree import _Element as Element, _ElementTree as ElementTree  # noqa

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
    specimen: set[BigpictureSpecimenFields] = Field(default_factory=set)
    staining: set[BigpictureStainingFields] = Field(default_factory=set)
    diagnosis: set[BigpictureCodeAttributeValue] = Field(default_factory=set)
    diagnosis_candidate: set[BigpictureCodeAttributeValue] = Field(default_factory=set)
    # Newest file modification date in the dataset.
    dataset_modified_at: datetime | None = None


def _has_value(value: Any) -> bool:
    """Whether a parsed field carries an indexable value."""
    if value is None:
        return False
    if isinstance(value, Set):
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
        elif isinstance(value, Set):
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
    for items in (fields.specimen, fields.staining):
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
OBSERVATION_XML_FILE = "METADATA/observation.xml"

XML_SCHEMA_DIR = (
    Path(__file__).resolve().parent.parent.parent / "schemas" / "bigpicture"
)

DATASET_XML_SCHEMA_FILE = "BP.dataset.xsd"
IMAGE_XML_SCHEMA_FILE = "BP.image.xsd"
SAMPLE_XML_SCHEMA_FILE = "BP.sample.xsd"
STAINING_XML_SCHEMA_FILE = "BP.staining.xsd"
OBSERVATION_XML_SCHEMA_FILE = "BP.observation.xsd"


def _is_snomed_scheme(scheme: str | None) -> bool:
    if scheme is None:
        return False
    return "".join(scheme.split()).lower() in {"snomedct", "snomed", "sct"}


_OBSERVATION_IMAGE_REF = "IMAGE_REF"
_OBSERVATION_SLIDE_REF = "SLIDE_REF"
_OBSERVATION_BLOCK_REF = "BLOCK_REF"
_OBSERVATION_SPECIMEN_REF = "SPECIMEN_REF"
_OBSERVATION_BIOLOGICAL_BEING_REF = "BIOLOGICAL_BEING_REF"
_OBSERVATION_CASE_REF = "CASE_REF"

_OBSERVATION_REFS = (
    _OBSERVATION_IMAGE_REF,
    _OBSERVATION_SLIDE_REF,
    _OBSERVATION_BLOCK_REF,
    _OBSERVATION_SPECIMEN_REF,
    _OBSERVATION_BIOLOGICAL_BEING_REF,
    _OBSERVATION_CASE_REF,
)


class ObjectKey(BaseModel):
    """Object alias or optional accession."""

    model_config = ConfigDict(frozen=True)

    kind: Literal["accession", "alias"]
    value: str


class ObjectIds(BaseModel):
    """Object alias and, if present, its accession."""

    model_config = ConfigDict(frozen=True)

    alias: str
    accession: str | None = None

    @property
    def id(self) -> str:
        """The accession if present, else the alias."""
        return self.accession or self.alias

    @property
    def keys(self) -> list[ObjectKey]:
        keys = [ObjectKey(kind="alias", value=self.alias)]
        if self.accession is not None:
            keys.append(ObjectKey(kind="accession", value=self.accession))
        return keys


# Maps object's key to the ids of each related object.
type IdMap = dict[ObjectKey, set[ObjectIds]]
# Maps object's key to image accessions if present, else the image aliases.
type ImageIdMap = dict[ObjectKey, set[str]]

RelatedIdType = TypeVar("RelatedIdType", ObjectIds, str)


def _related_ids(
    keys: Iterable[ObjectKey], id_map: dict[ObjectKey, set[RelatedIdType]]
) -> set[RelatedIdType]:
    return {related_id for key in keys for related_id in id_map.get(key, set())}


def _object_keys(source: Element | Iterable[ObjectIds]) -> Sequence[ObjectKey]:
    if isinstance(source, Element):
        return [
            ObjectKey(kind=attribute, value=value)
            for attribute in ("accession", "alias")
            if (value := source.get(attribute)) is not None
        ]
    return [key for ids in source for key in ids.keys]


def _object_ids(elem: Element) -> ObjectIds:
    """Return an object's alias and optional accession."""
    return ObjectIds(
        alias=cast(str, elem.get("alias")), accession=elem.get("accession")
    )


def _map_ref(
    elem: Element,
    ref_tag: str,
    id_map: dict[ObjectKey, set[RelatedIdType]],
    value: RelatedIdType,
) -> None:
    """Map reference aliases and optional accessions to the provided value."""
    for ref in elem.xpath(f"./{ref_tag}"):
        for key in _object_keys(ref):
            id_map.setdefault(key, set()).add(value)


def _image_ids_from_slides(
    slide_keys: Iterable[ObjectKey], map_slide_key_to_image_ids: ImageIdMap
) -> set[str]:
    return _related_ids(slide_keys, map_slide_key_to_image_ids)


def _image_ids_from_blocks(
    block_keys: Iterable[ObjectKey],
    map_block_key_to_slide_ids: IdMap,
    map_slide_key_to_image_ids: ImageIdMap,
) -> set[str]:
    slides = _related_ids(block_keys, map_block_key_to_slide_ids)
    return _image_ids_from_slides(_object_keys(slides), map_slide_key_to_image_ids)


def _image_ids_from_specimens(
    specimen_keys: Iterable[ObjectKey],
    map_specimen_key_to_block_ids: IdMap,
    map_block_key_to_slide_ids: IdMap,
    map_slide_key_to_image_ids: ImageIdMap,
) -> set[str]:
    blocks = _related_ids(specimen_keys, map_specimen_key_to_block_ids)
    return _image_ids_from_blocks(
        _object_keys(blocks), map_block_key_to_slide_ids, map_slide_key_to_image_ids
    )


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
    single_dir: bool = False,
    c4gh_private_key_file: str | None = None,
    c4gh_passphrase: str | None = None,
) -> Iterator[ExtractedDocument]:
    """
    Extract search fields from Bigpicture XML directories under the root path.

    :param root: Root directory or bucket path.
    :param fs: Optional fsspec filesystem. If None, a local filesystem is used.
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
            yield from extract_dataset_documents(d, fs, keys)
        except Exception:
            logging.error("Failed to extract fields from dataset %s.", d, exc_info=True)
            raise


def extract_dataset_documents(
    root: str,
    fs: fsspec.AbstractFileSystem | None = None,
    keys: list | None = None,
) -> Iterator[ExtractedDocument]:
    """
    Extract search fields from a single Bigpicture XML dataset directory.

    :param root: Dataset directory path.
    :param fs: Optional fsspec filesystem. If None, a local filesystem is used.
    :param keys: Crypt4GH keys for decrypting ``.c4gh`` files, as returned by
        ``load_c4gh_keys``. If None, only plain files are accepted.
    """
    if fs is None:
        # Use local filesystem.
        fs = fsspec.filesystem("file")

    dataset_file_path = resolve_path(fs, f"{root}/{DATASET_XML_FILE}")
    image_file_path = resolve_path(fs, f"{root}/{IMAGE_XML_FILE}")
    sample_file_path = resolve_path(fs, f"{root}/{SAMPLE_XML_FILE}")
    staining_file_path = resolve_path(fs, f"{root}/{STAINING_XML_FILE}")
    observation_file_path = resolve_path(
        fs, f"{root}/{OBSERVATION_XML_FILE}", optional=True
    )

    dataset_modified_at = _get_last_modification_time(
        fs,
        [
            file_path
            for file_path in (
                dataset_file_path,
                image_file_path,
                sample_file_path,
                staining_file_path,
                observation_file_path,
            )
            if file_path is not None
        ],
    )

    # Map other ids to image ids.

    images: list[ObjectIds] = []
    map_image_id_to_document_id: dict[str, str] = {}
    map_image_key_to_image_ids: ImageIdMap = {}
    map_slide_key_to_image_ids: ImageIdMap = {}
    map_block_key_to_slide_ids: IdMap = {}
    map_staining_key_to_slide_ids: IdMap = {}
    map_specimen_key_to_block_ids: IdMap = {}
    map_biological_being_key_to_specimen_ids: IdMap = {}
    map_case_key_to_specimen_ids: IdMap = {}
    map_block_id_to_fields: dict[str, BigpictureSampleBlockFields] = {}
    map_specimen_id_to_fields: dict[str, BigpictureSampleSpecimenFields] = {}
    list_biological_being_fields: list[
        tuple[ObjectIds, BigpictureSampleBiologicalBeingFields]
    ] = []
    list_staining_fields: list[tuple[ObjectIds, list[BigpictureStainingFields]]] = []

    # Read dataset XML.
    #

    dataset_xml = parse_xml(read_file(fs, dataset_file_path, keys))
    validate_xml(dataset_xml, XML_SCHEMA_DIR, DATASET_XML_SCHEMA_FILE)
    # The dataset is identified by its accession.
    dataset_id = get_xml_value(
        "/DATASET/@accession | /DATASET_SET/DATASET/@accession",
        dataset_xml,
        optional=True,
    )
    if dataset_id is None:
        raise ValueError(
            f"Failed to extract dataset accession from {str(dataset_file_path)}"
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

    for xml in image_xml.xpath("/IMAGE | /IMAGE_SET/IMAGE"):
        image_ids = _object_ids(xml)
        images.append(image_ids)
        # The document id is image accession if present, otherwise image
        # alias prefixed with dataset accession to make it unique.
        map_image_id_to_document_id[image_ids.id] = (
            image_ids.accession
            if image_ids.accession
            else f"{dataset_id}-{image_ids.alias}"
        )
        for key in image_ids.keys:
            map_image_key_to_image_ids.setdefault(key, set()).add(image_ids.id)
        _map_ref(xml, "IMAGE_OF", map_slide_key_to_image_ids, image_ids.id)

    # Read sample XML.
    #

    sample_xml = parse_xml(read_file(fs, sample_file_path, keys))
    validate_xml(sample_xml, XML_SCHEMA_DIR, SAMPLE_XML_SCHEMA_FILE)

    for xml in sample_xml.xpath("/SLIDE | /SAMPLE_SET/SLIDE"):
        slide_ids = _object_ids(xml)
        _map_ref(xml, "CREATED_FROM_REF", map_block_key_to_slide_ids, slide_ids)
        _map_ref(
            xml,
            "STAINING_INFORMATION_REF",
            map_staining_key_to_slide_ids,
            slide_ids,
        )

    for xml in sample_xml.xpath("/BLOCK | /SAMPLE_SET/BLOCK"):
        block_ids = _object_ids(xml)
        # Extract fields from XML.
        map_block_id_to_fields[block_ids.id] = _extract_sample_block_fields(xml)
        _map_ref(xml, "SAMPLED_FROM_REF", map_specimen_key_to_block_ids, block_ids)

    for xml in sample_xml.xpath("/SPECIMEN | /SAMPLE_SET/SPECIMEN"):
        specimen_ids = _object_ids(xml)
        # Extract fields from XML.
        map_specimen_id_to_fields[specimen_ids.id] = _extract_sample_specimen_fields(
            xml
        )
        _map_ref(
            xml,
            "EXTRACTED_FROM_REF",
            map_biological_being_key_to_specimen_ids,
            specimen_ids,
        )
        _map_ref(xml, "PART_OF_CASE_REF", map_case_key_to_specimen_ids, specimen_ids)

    for xml in sample_xml.xpath("/BIOLOGICAL_BEING | /SAMPLE_SET/BIOLOGICAL_BEING"):
        # Extract fields from XML.
        being_ids = _object_ids(xml)
        list_biological_being_fields.append(
            (being_ids, _extract_sample_biological_being_fields(xml))
        )

    # Read staining XML.
    #

    staining_xml = parse_xml(read_file(fs, staining_file_path, keys))
    validate_xml(staining_xml, XML_SCHEMA_DIR, STAINING_XML_SCHEMA_FILE)

    for xml in staining_xml.xpath("/STAINING | /STAINING_SET/STAINING"):
        # Extract fields from XML.
        staining_ids = _object_ids(xml)
        list_staining_fields.append((staining_ids, _extract_staining_fields(xml)))

    # Finished reading XMLs.

    # Add dataset fields.
    fields: dict[str, BigpictureFields] = {}
    dataset_image_cnt = len(images)
    for image_ids in images:
        image_id = image_ids.id
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
        biological_being_ids,
        biological_being_fields,
    ) in list_biological_being_fields:
        for specimen_ids in _related_ids(
            biological_being_ids.keys, map_biological_being_key_to_specimen_ids
        ):
            for block_ids in _related_ids(
                specimen_ids.keys, map_specimen_key_to_block_ids
            ):
                specimen = BigpictureSpecimenFields(
                    **map_block_id_to_fields[block_ids.id].model_dump(),
                    **map_specimen_id_to_fields[specimen_ids.id].model_dump(),
                    **biological_being_fields.model_dump(),
                )
                for image_id in _image_ids_from_blocks(
                    block_ids.keys,
                    map_block_key_to_slide_ids,
                    map_slide_key_to_image_ids,
                ):
                    fields[image_id].specimen.add(specimen)

    # Add staining fields.
    for (
        staining_ids,
        staining_fields_list,
    ) in list_staining_fields:
        stained_slide_keys = _object_keys(
            _related_ids(staining_ids.keys, map_staining_key_to_slide_ids)
        )
        for staining_fields in staining_fields_list:
            if any(v is not None for v in staining_fields.model_dump().values()):
                for image_id in _image_ids_from_slides(
                    stained_slide_keys, map_slide_key_to_image_ids
                ):
                    fields[image_id].staining.add(staining_fields)

    # TODO: support non-clinical datasets.
    is_clinical = True

    # Add observation fields.
    if is_clinical and observation_file_path is not None:

        def _images_for_ref(_tag: str, ref_keys: Sequence[ObjectKey]) -> set[str]:
            if _tag == _OBSERVATION_IMAGE_REF:
                return _related_ids(ref_keys, map_image_key_to_image_ids)
            if _tag == _OBSERVATION_SLIDE_REF:
                return _image_ids_from_slides(ref_keys, map_slide_key_to_image_ids)
            if _tag == _OBSERVATION_BLOCK_REF:
                return _image_ids_from_blocks(
                    ref_keys,
                    map_block_key_to_slide_ids,
                    map_slide_key_to_image_ids,
                )
            if _tag == _OBSERVATION_SPECIMEN_REF:
                return _image_ids_from_specimens(
                    ref_keys,
                    map_specimen_key_to_block_ids,
                    map_block_key_to_slide_ids,
                    map_slide_key_to_image_ids,
                )
            if _tag == _OBSERVATION_BIOLOGICAL_BEING_REF:
                return _image_ids_from_specimens(
                    _object_keys(
                        _related_ids(ref_keys, map_biological_being_key_to_specimen_ids)
                    ),
                    map_specimen_key_to_block_ids,
                    map_block_key_to_slide_ids,
                    map_slide_key_to_image_ids,
                )
            if _tag == _OBSERVATION_CASE_REF:
                return _image_ids_from_specimens(
                    _object_keys(_related_ids(ref_keys, map_case_key_to_specimen_ids)),
                    map_specimen_key_to_block_ids,
                    map_block_key_to_slide_ids,
                    map_slide_key_to_image_ids,
                )
            return set()

        observation_xml = parse_xml(read_file(fs, observation_file_path, keys))
        validate_xml(observation_xml, XML_SCHEMA_DIR, OBSERVATION_XML_SCHEMA_FILE)
        for observation in observation_xml.xpath(
            "/OBSERVATION | /OBSERVATION_SET/OBSERVATION"
        ):
            statement = observation.find("STATEMENT")
            if statement is None or statement.findtext("STATEMENT_TYPE") != "Diagnosis":
                continue
            codes = _extract_diagnoses(statement)
            if not codes:
                continue
            status = statement.findtext("STATEMENT_STATUS")
            for tag in _OBSERVATION_REFS:
                ref = observation.find(tag)
                if ref is None:
                    continue
                # Diagnosis linked directly to an image is considered distinct
                # regardless of statement status.
                distinct = tag == _OBSERVATION_IMAGE_REF or status == "Distinct"
                field_name = "diagnosis" if distinct else "diagnosis_candidate"
                for image_id in _images_for_ref(tag, _object_keys(ref)):
                    getattr(fields[image_id], field_name).update(codes)
                break

    # Return iterator of extracted documents.
    for image_ids in images:
        image_id = image_ids.id
        bp_fields = fields[image_id]
        yield ExtractedDocument(
            id=map_image_id_to_document_id[image_id],
            modified_at=bp_fields.dataset_modified_at,
            values=to_opensearch_field_values(bp_fields),
        )


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


def _code_attribute_value(value: ElementTree) -> BigpictureCodeAttributeValue:
    return BigpictureCodeAttributeValue(
        code=value.findtext("CODE"),
        scheme=value.findtext("SCHEME"),
        meaning=value.findtext("MEANING"),
        scheme_version=value.findtext("SCHEME_VERSION"),
    )


def _extract_code_attribute_value(
    elem: ElementTree, tag: str, *, is_attributes=True
) -> BigpictureCodeAttributeValue | None:
    if is_attributes:
        values = elem.xpath(f"ATTRIBUTES/CODE_ATTRIBUTE[TAG='{tag}']/VALUE")
    else:
        values = elem.xpath(f"CODE_ATTRIBUTE[TAG='{tag}']/VALUE")

    if not values or _is_nil(values[0]):
        return None

    return _code_attribute_value(values[0])


def _extract_code_attribute_values(
    elem: ElementTree, tag: str, *, is_attributes=True
) -> frozenset[BigpictureCodeAttributeValue]:
    if is_attributes:
        values = elem.xpath(f"ATTRIBUTES/CODE_ATTRIBUTE[TAG='{tag}']/VALUE")
    else:
        values = elem.xpath(f"CODE_ATTRIBUTE[TAG='{tag}']/VALUE")

    return frozenset(_code_attribute_value(v) for v in values if not _is_nil(v))


def _extract_diagnoses(
    statement: ElementTree,
) -> set[BigpictureCodeAttributeValue]:
    codes = {
        _code_attribute_value(v)
        for v in statement.xpath("CODE_ATTRIBUTES/CODE_ATTRIBUTE/VALUE")
        if not _is_nil(v)
    }
    return {c for c in codes if _is_snomed_scheme(c.scheme)}


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
