"""Bigpicture document XML extraction."""

import logging
from collections.abc import Iterable, Iterator, Set
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import fsspec  # type: ignore
from lxml.etree import _Element as Element, _ElementTree as ElementTree  # noqa
from pydantic import BaseModel, ConfigDict

from search_api.api.bigpicture.models import BP_DOCUMENT_FIELDS
from search_api.api.bigpicture.extract.values import (
    extract_diagnoses,
    extract_finding,
    extract_sample_biological_being_fields,
    extract_sample_block_fields,
    extract_sample_specimen_fields,
    extract_scope,
    extract_staining_fields,
)
from search_api.api.bigpicture.extract.models import (
    BigpictureExtractedObject,
    OBSERVATION_CANDIDATE,
    OBSERVATION_CONFIRMED,
    OBSERVATION_QUALIFIER,
    BigpictureCodeAttributeValue,
    BigpictureDiagnosisFields,
    BigpictureFields,
    BigpictureSampleBiologicalBeingFields,
    BigpictureSampleBlockFields,
    BigpictureSampleSpecimenFields,
    BigpictureSpecimenFields,
    BigpictureStainingFields,
    ObjectIds,
    NESTED_GROUPS,
)
from search_api.api.bigpicture.extract.refs import (
    OBSERVATION_IMAGE_REF,
    OBSERVATION_REFS,
    BigpictureReferences,
    map_ref,
    object_ids,
    object_keys,
    related_ids,
)
from search_api.api.extract_logs import ExtractLog
from search_api.api.opensearch.models import (
    ExtractedDocument,
    OpenSearchFieldValue,
    OpenSearchGroup,
)
from search_api.api.qualifiers import QUALIFIERS_FIELD
from search_api.exceptions import SystemException
from search_api.utils.crypt import read_file, resolve_path
from search_api.utils.xml import get_xml_value, parse_xml, validate_xml


def _has_value(value: Any) -> bool:
    """Whether a parsed field carries an indexable value."""
    if value is None:
        return False
    if isinstance(value, Set):
        return len(value) > 0
    return True


def to_opensearch_values(
    fields: BigpictureFields,
) -> tuple[list[OpenSearchFieldValue], list[OpenSearchGroup]]:
    """Convert extracted field models to a document's top level values and nested groups."""

    def field_value(field_name: str, value: Any) -> list[OpenSearchFieldValue]:
        if not _has_value(value):
            return []
        field = BP_DOCUMENT_FIELDS.get(field_name)
        if field is None:
            raise SystemException(
                f"Field {field_name!r} is not registered in BP_DOCUMENT_FIELDS"
            )
        if isinstance(value, BigpictureCodeAttributeValue):
            return [
                OpenSearchFieldValue(field=field, value=(value.code, value.meaning))
            ]
        if isinstance(value, Set) and all(
            isinstance(code, BigpictureCodeAttributeValue) for code in value
        ):
            # A multivalued field of code attributes.
            return [
                OpenSearchFieldValue(field=field, value=(code.code, code.meaning))
                for code in value
            ]
        if isinstance(value, (tuple, int, str)):
            return [OpenSearchFieldValue(field=field, value=value)]
        raise SystemException(
            f"Field {field_name!r} has an unexpected value type: {type(value).__name__!r}"
        )

    values: list[OpenSearchFieldValue] = []
    for field_name in type(fields).model_fields:
        if field_name in BP_DOCUMENT_FIELDS and field_name not in NESTED_GROUPS:
            values += field_value(field_name, getattr(fields, field_name))

    groups: list[OpenSearchGroup] = []
    for group in NESTED_GROUPS:
        for item in getattr(fields, group):
            item_values: list[OpenSearchFieldValue] = []
            for field_name in type(item).model_fields:
                if field_name == QUALIFIERS_FIELD:
                    continue
                item_values += field_value(field_name, getattr(item, field_name))
            if not item_values:
                # A group carrying no indexable field values is not indexed.
                continue
            groups.append(
                OpenSearchGroup(
                    group=group,
                    values=item_values,
                    qualifiers=dict(getattr(item, QUALIFIERS_FIELD, ())),
                )
            )

    return values, groups


DATASET_XML_FILE = "METADATA/dataset.xml"
IMAGE_XML_FILE = "METADATA/image.xml"
POLICY_XML_FILE = "METADATA/policy.xml"
SAMPLE_XML_FILE = "METADATA/sample.xml"
STAINING_XML_FILE = "METADATA/staining.xml"
OBSERVATION_XML_FILE = "METADATA/observation.xml"

XML_SCHEMA_DIR = Path(__file__).resolve().parent.parent / "schemas"

DATASET_XML_SCHEMA_FILE = "BP.dataset.xsd"
IMAGE_XML_SCHEMA_FILE = "BP.image.xsd"
POLICY_XML_SCHEMA_FILE = "BP.policy.xsd"
SAMPLE_XML_SCHEMA_FILE = "BP.sample.xsd"
STAINING_XML_SCHEMA_FILE = "BP.staining.xsd"
OBSERVATION_XML_SCHEMA_FILE = "BP.observation.xsd"


def get_last_modification_time(
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


class DatasetFiles(BaseModel):
    """The metadata files of one dataset directory, plain or Crypt4GH-encrypted."""

    model_config = ConfigDict(frozen=True)

    dataset: str
    image: str
    policy: str
    sample: str
    staining: str
    observation: str | None

    @property
    def paths(self) -> list[str]:
        """Get every file path of the dataset."""
        return [path for path in self.model_dump().values() if path is not None]


def dataset_files(fs: fsspec.AbstractFileSystem, root: str) -> DatasetFiles:
    """Get the metadata files of one dataset directory.

    :param fs: The filesystem.
    :param root: Dataset directory path.
    """
    return DatasetFiles(
        dataset=resolve_path(fs, f"{root}/{DATASET_XML_FILE}"),
        image=resolve_path(fs, f"{root}/{IMAGE_XML_FILE}"),
        policy=resolve_path(fs, f"{root}/{POLICY_XML_FILE}"),
        sample=resolve_path(fs, f"{root}/{SAMPLE_XML_FILE}"),
        staining=resolve_path(fs, f"{root}/{STAINING_XML_FILE}"),
        observation=resolve_path(fs, f"{root}/{OBSERVATION_XML_FILE}", optional=True),
    )


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

    files = dataset_files(fs, root)
    modified_at = get_last_modification_time(fs, files.paths)

    # Map other ids to image ids.

    images: list[ObjectIds] = []
    map_image_id_to_document_id: dict[str, str] = {}
    references = BigpictureReferences()
    blocks: dict[str, BigpictureExtractedObject[BigpictureSampleBlockFields]] = {}
    specimens: dict[str, BigpictureExtractedObject[BigpictureSampleSpecimenFields]] = {}
    beings: list[BigpictureExtractedObject[BigpictureSampleBiologicalBeingFields]] = []
    stainings: list[BigpictureExtractedObject[list[BigpictureStainingFields]]] = []

    # Logs are collected by image id.
    logs_by_image_id: dict[str, list[ExtractLog]] = {}

    def _add_logs(_image_ids: Iterable[str], _logs: list[ExtractLog]) -> None:
        """Add extract logs for each image."""
        for _image_id in _image_ids:
            logs_by_image_id.setdefault(_image_id, []).extend(_logs)

    # Read dataset XML.
    #

    dataset_xml = parse_xml(read_file(fs, files.dataset, keys))
    validate_xml(dataset_xml, XML_SCHEMA_DIR, DATASET_XML_SCHEMA_FILE)
    # The dataset is identified by its accession.
    dataset_id = get_xml_value(
        "/DATASET/@accession | /DATASET_SET/DATASET/@accession",
        dataset_xml,
        optional=True,
    )
    if dataset_id is None:
        raise ValueError(
            f"Failed to extract dataset accession from {str(files.dataset)}"
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

    image_xml = parse_xml(read_file(fs, files.image, keys))
    validate_xml(image_xml, XML_SCHEMA_DIR, IMAGE_XML_SCHEMA_FILE)

    for xml in image_xml.xpath("/IMAGE | /IMAGE_SET/IMAGE"):
        image_ids = object_ids(xml)
        images.append(image_ids)
        # The document id is image accession if present, otherwise image
        # alias prefixed with dataset accession to make it unique.
        map_image_id_to_document_id[image_ids.id] = (
            image_ids.accession
            if image_ids.accession
            else f"{dataset_id}-{image_ids.alias}"
        )
        for key in image_ids.keys:
            references.image_key_to_image_ids.setdefault(key, set()).add(image_ids.id)
        map_ref(xml, "IMAGE_OF", references.slide_key_to_image_ids, image_ids.id)

    # Read policy XML.
    #

    policy_xml = parse_xml(read_file(fs, files.policy, keys))
    validate_xml(policy_xml, XML_SCHEMA_DIR, POLICY_XML_SCHEMA_FILE)
    scope = extract_scope(policy_xml, files.policy)
    is_clinical = scope == "clinical"

    # Read sample XML.
    #

    sample_xml = parse_xml(read_file(fs, files.sample, keys))
    validate_xml(sample_xml, XML_SCHEMA_DIR, SAMPLE_XML_SCHEMA_FILE)

    for xml in sample_xml.xpath("/SLIDE | /SAMPLE_SET/SLIDE"):
        slide_ids = object_ids(xml)
        map_ref(xml, "CREATED_FROM_REF", references.block_key_to_slide_ids, slide_ids)
        map_ref(
            xml,
            "STAINING_INFORMATION_REF",
            references.staining_key_to_slide_ids,
            slide_ids,
        )

    for xml in sample_xml.xpath("/BLOCK | /SAMPLE_SET/BLOCK"):
        # Extract fields from XML.
        block = extract_sample_block_fields(xml)
        blocks[block.ids.id] = block
        map_ref(
            xml, "SAMPLED_FROM_REF", references.specimen_key_to_block_ids, block.ids
        )

    for xml in sample_xml.xpath("/SPECIMEN | /SAMPLE_SET/SPECIMEN"):
        # Extract fields from XML.
        specimen = extract_sample_specimen_fields(xml)
        specimens[specimen.ids.id] = specimen
        map_ref(
            xml,
            "EXTRACTED_FROM_REF",
            references.being_key_to_specimen_ids,
            specimen.ids,
        )
        map_ref(
            xml, "PART_OF_CASE_REF", references.case_key_to_specimen_ids, specimen.ids
        )

    for xml in sample_xml.xpath("/BIOLOGICAL_BEING | /SAMPLE_SET/BIOLOGICAL_BEING"):
        # Extract fields from XML.
        beings.append(
            extract_sample_biological_being_fields(xml, is_clinical=is_clinical)
        )

    # Read staining XML.
    #

    staining_xml = parse_xml(read_file(fs, files.staining, keys))
    validate_xml(staining_xml, XML_SCHEMA_DIR, STAINING_XML_SCHEMA_FILE)

    for xml in staining_xml.xpath("/STAINING | /STAINING_SET/STAINING"):
        # Extract fields from XML.
        stainings.append(extract_staining_fields(xml))

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
            scope=scope,
            dataset_short_name=dataset_short_name if is_clinical else None,
            dataset_title=dataset_title,
            dataset_description=dataset_description,
            dataset_modified_at=modified_at,
        )

    # Add specimen fields. A specimen is extracted from exactly one
    # biological being, and its block has a single field, so both are
    # flattened into the specimen.
    for being in beings:
        for specimen_ids in related_ids(
            being.ids.keys, references.being_key_to_specimen_ids
        ):
            for block_ids in related_ids(
                specimen_ids.keys, references.specimen_key_to_block_ids
            ):
                block = blocks[block_ids.id]
                specimen = specimens[specimen_ids.id]
                specimen_fields = BigpictureSpecimenFields(
                    **block.fields.model_dump(),
                    **specimen.fields.model_dump(),
                    **being.fields.model_dump(),
                )
                specimen_image_ids = references.image_ids_from_blocks(block_ids.keys)
                for image_id in specimen_image_ids:
                    fields[image_id].specimen.add(specimen_fields)
                _add_logs(specimen_image_ids, block.logs + specimen.logs + being.logs)

    # Add staining fields.
    for staining in stainings:
        staining_image_ids = references.image_ids_from_stainings(staining.ids.keys)
        _add_logs(staining_image_ids, staining.logs)
        for staining_fields in staining.fields:
            if any(v is not None for v in staining_fields.model_dump().values()):
                for image_id in staining_image_ids:
                    fields[image_id].staining.add(staining_fields)

    # Add observation fields. A clinical dataset carries diagnoses, a
    # non-clinical one findings; the statement type decides which.
    if files.observation is not None:
        observation_xml = parse_xml(read_file(fs, files.observation, keys))
        validate_xml(observation_xml, XML_SCHEMA_DIR, OBSERVATION_XML_SCHEMA_FILE)

        for observation in observation_xml.xpath(
            "/OBSERVATION | /OBSERVATION_SET/OBSERVATION"
        ):
            statement = observation.find("STATEMENT")
            if statement is None:
                continue
            statement_type = statement.findtext("STATEMENT_TYPE")
            if statement_type not in ("Diagnosis", "Finding"):
                continue
            status = statement.findtext("STATEMENT_STATUS")
            for tag, image_ids_for_ref in OBSERVATION_REFS.items():
                ref = observation.find(tag)
                if ref is None:
                    continue
                # A statement is always considered confirmed if it is linked directly
                # to an image.
                confirmed = tag == OBSERVATION_IMAGE_REF or status == "Distinct"
                qualifier_value = (
                    OBSERVATION_CONFIRMED if confirmed else OBSERVATION_CANDIDATE
                )
                ref_image_ids = image_ids_for_ref(references, object_keys(ref))
                qualifier = {
                    QUALIFIERS_FIELD: frozenset(
                        {(OBSERVATION_QUALIFIER, qualifier_value)}
                    )
                }
                if statement_type == "Diagnosis":
                    group = "diagnosis"
                    diagnoses, statement_logs = extract_diagnoses(statement)
                    items: list[BaseModel] = [
                        BigpictureDiagnosisFields(diagnosis=code, **qualifier)
                        for code in diagnoses
                    ]
                else:
                    group = "finding"
                    finding, statement_logs = extract_finding(statement, **qualifier)
                    items = [finding] if finding is not None else []
                for ref_image_id in ref_image_ids:
                    getattr(fields[ref_image_id], group).update(items)
                _add_logs(ref_image_ids, statement_logs)
                break

    # Return iterator of extracted documents.
    for image_ids in images:
        image_id = image_ids.id
        bp_fields = fields[image_id]
        values, groups = to_opensearch_values(bp_fields)
        yield ExtractedDocument(
            id=map_image_id_to_document_id[image_id],
            modified_at=bp_fields.dataset_modified_at,
            values=values,
            groups=groups,
            scope=bp_fields.scope,
            logs=logs_by_image_id.get(image_id, []),
        )
