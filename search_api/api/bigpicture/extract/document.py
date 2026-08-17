"""Bigpicture document XML extraction."""

import logging
from collections.abc import Iterable, Iterator, Set
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Generic, Literal, TypeVar, cast

import fsspec  # type: ignore
from lxml.etree import _Element as Element, _ElementTree as ElementTree  # noqa
from pydantic import BaseModel

from search_api.api.beacon.models import SNOMED_ONTOLOGY_ID
from search_api.api.bigpicture.models import BP_DOCUMENT_FIELDS
from search_api.api.bigpicture.extract.attributes import (
    _FINDING_FIELD_IDS,
    _code_attribute_value,
    _extract_age_at_extraction,
    _extract_anatomical_sites,
    _extract_code_attribute_value,
    _extract_fixation_type,
    _extract_string_attribute_value,
    _filter_values_by_scheme,
    _is_nil,
)
from search_api.api.bigpicture.extract.models import (
    OBSERVATION_CANDIDATE,
    OBSERVATION_CONFIRMED,
    OBSERVATION_QUALIFIER,
    BigpictureCodeAttributeValue,
    BigpictureDiagnosisFields,
    BigpictureFields,
    BigpictureFindingFields,
    BigpictureSampleBiologicalBeingFields,
    BigpictureSampleBlockFields,
    BigpictureSampleSpecimenFields,
    BigpictureSpecimenFields,
    BigpictureStainingFields,
    ObjectIds,
    _NESTED_GROUPS,
)
from search_api.api.bigpicture.extract.refs import (
    _OBSERVATION_IMAGE_REF,
    _OBSERVATION_REFS,
    _References,
    _map_ref,
    _object_ids,
    _object_keys,
    _related_ids,
)
from search_api.api.extract_logs import ExtractLog
from search_api.api.opensearch.models import (
    ExtractedDocument,
    OpenSearchFieldValue,
    OpenSearchGroup,
)
from search_api.api.qualifiers import QUALIFIERS_FIELD
from search_api.exceptions import SystemException, UserException
from search_api.services.ontology.send import SEND_ONTOLOGY_ID
from search_api.utils.crypt import load_c4gh_keys, read_file, resolve_path
from search_api.utils.dir import list_directories
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
        if field_name in BP_DOCUMENT_FIELDS and field_name not in _NESTED_GROUPS:
            values += field_value(field_name, getattr(fields, field_name))

    groups: list[OpenSearchGroup] = []
    for group in _NESTED_GROUPS:
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

FieldsT = TypeVar("FieldsT")


@dataclass(frozen=True)
class _Extracted(Generic[FieldsT]):
    """One extracted XML object.

    A dataclass rather than a pydantic model to avoid copying the logs.
    """

    ids: ObjectIds
    fields: FieldsT
    logs: list[ExtractLog]


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


_TYPE_OF_DATASET_TAG = "type_of_dataset"


# Scope by lower-cased part of 'type_of_dataset' string attribute before the "/".
_SCOPE_BY_DATASET_TYPE: dict[str, Literal["clinical", "non_clinical"]] = {
    "clinical": "clinical",
    "non-clinical": "non_clinical",
}


def _extract_scope(
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
    policy_file_path = resolve_path(fs, f"{root}/{POLICY_XML_FILE}")
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
                policy_file_path,
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
    references = _References()
    blocks: dict[str, _Extracted[BigpictureSampleBlockFields]] = {}
    specimens: dict[str, _Extracted[BigpictureSampleSpecimenFields]] = {}
    beings: list[_Extracted[BigpictureSampleBiologicalBeingFields]] = []
    stainings: list[_Extracted[list[BigpictureStainingFields]]] = []

    # Logs are collected by image id.
    logs_by_image_id: dict[str, list[ExtractLog]] = {}

    def _add_logs(_image_ids: Iterable[str], _logs: list[ExtractLog]) -> None:
        """Add extract logs for each image."""
        for _image_id in _image_ids:
            logs_by_image_id.setdefault(_image_id, []).extend(_logs)

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
            references.image_key_to_image_ids.setdefault(key, set()).add(image_ids.id)
        _map_ref(xml, "IMAGE_OF", references.slide_key_to_image_ids, image_ids.id)

    # Read policy XML.
    #

    policy_xml = parse_xml(read_file(fs, policy_file_path, keys))
    validate_xml(policy_xml, XML_SCHEMA_DIR, POLICY_XML_SCHEMA_FILE)
    scope = _extract_scope(policy_xml, policy_file_path)
    is_clinical = scope == "clinical"

    # Read sample XML.
    #

    sample_xml = parse_xml(read_file(fs, sample_file_path, keys))
    validate_xml(sample_xml, XML_SCHEMA_DIR, SAMPLE_XML_SCHEMA_FILE)

    for xml in sample_xml.xpath("/SLIDE | /SAMPLE_SET/SLIDE"):
        slide_ids = _object_ids(xml)
        _map_ref(xml, "CREATED_FROM_REF", references.block_key_to_slide_ids, slide_ids)
        _map_ref(
            xml,
            "STAINING_INFORMATION_REF",
            references.staining_key_to_slide_ids,
            slide_ids,
        )

    for xml in sample_xml.xpath("/BLOCK | /SAMPLE_SET/BLOCK"):
        # Extract fields from XML.
        block = _extract_sample_block_fields(xml)
        blocks[block.ids.id] = block
        _map_ref(
            xml, "SAMPLED_FROM_REF", references.specimen_key_to_block_ids, block.ids
        )

    for xml in sample_xml.xpath("/SPECIMEN | /SAMPLE_SET/SPECIMEN"):
        # Extract fields from XML.
        specimen = _extract_sample_specimen_fields(xml)
        specimens[specimen.ids.id] = specimen
        _map_ref(
            xml,
            "EXTRACTED_FROM_REF",
            references.being_key_to_specimen_ids,
            specimen.ids,
        )
        _map_ref(
            xml, "PART_OF_CASE_REF", references.case_key_to_specimen_ids, specimen.ids
        )

    for xml in sample_xml.xpath("/BIOLOGICAL_BEING | /SAMPLE_SET/BIOLOGICAL_BEING"):
        # Extract fields from XML.
        beings.append(_extract_sample_biological_being_fields(xml))

    # Read staining XML.
    #

    staining_xml = parse_xml(read_file(fs, staining_file_path, keys))
    validate_xml(staining_xml, XML_SCHEMA_DIR, STAINING_XML_SCHEMA_FILE)

    for xml in staining_xml.xpath("/STAINING | /STAINING_SET/STAINING"):
        # Extract fields from XML.
        stainings.append(_extract_staining_fields(xml))

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
            dataset_modified_at=dataset_modified_at,
        )

    # Add specimen fields. A specimen is extracted from exactly one
    # biological being, and its block has a single field, so both are
    # flattened into the specimen.
    for being in beings:
        for specimen_ids in _related_ids(
            being.ids.keys, references.being_key_to_specimen_ids
        ):
            for block_ids in _related_ids(
                specimen_ids.keys, references.specimen_key_to_block_ids
            ):
                being_values = being.fields.model_dump()
                if is_clinical:
                    # Animal species is a non-clinical field only.
                    being_values["animal_species"] = None
                block = blocks[block_ids.id]
                specimen = specimens[specimen_ids.id]
                specimen_fields = BigpictureSpecimenFields(
                    **block.fields.model_dump(),
                    **specimen.fields.model_dump(),
                    **being_values,
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
    if observation_file_path is not None:
        observation_xml = parse_xml(read_file(fs, observation_file_path, keys))
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
            for tag, image_ids_for_ref in _OBSERVATION_REFS.items():
                ref = observation.find(tag)
                if ref is None:
                    continue
                # A statement is always considered confirmed if it is linked directly
                # to an image.
                confirmed = tag == _OBSERVATION_IMAGE_REF or status == "Distinct"
                qualifier_value = (
                    OBSERVATION_CONFIRMED if confirmed else OBSERVATION_CANDIDATE
                )
                ref_image_ids = image_ids_for_ref(references, _object_keys(ref))
                qualifier = {
                    QUALIFIERS_FIELD: frozenset(
                        {(OBSERVATION_QUALIFIER, qualifier_value)}
                    )
                }
                statement_logs: list[ExtractLog] = []
                if statement_type == "Diagnosis":
                    group = "diagnosis"
                    items: list[BaseModel] = [
                        BigpictureDiagnosisFields(diagnosis=code, **qualifier)
                        for code in _extract_diagnoses(statement, statement_logs)
                    ]
                else:
                    group = "finding"
                    finding = _extract_finding(statement, statement_logs, **qualifier)
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


def _extract_sample_block_fields(
    xml: Element,
) -> _Extracted[BigpictureSampleBlockFields]:
    logs: list[ExtractLog] = []
    return _Extracted(
        ids=_object_ids(xml),
        fields=BigpictureSampleBlockFields(
            block_preparation=_extract_code_attribute_value(
                xml, "block_preparation", SNOMED_ONTOLOGY_ID, logs
            )
        ),
        logs=logs,
    )


def _extract_sample_biological_being_fields(
    xml: Element,
) -> _Extracted[BigpictureSampleBiologicalBeingFields]:
    logs: list[ExtractLog] = []
    return _Extracted(
        ids=_object_ids(xml),
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


def _extract_sample_specimen_fields(
    xml: Element,
) -> _Extracted[BigpictureSampleSpecimenFields]:
    logs: list[ExtractLog] = []
    fixation_type, fixation_type_text = _extract_fixation_type(xml, logs)

    return _Extracted(
        ids=_object_ids(xml),
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


def _extract_staining_fields(
    xml: Element,
) -> _Extracted[list[BigpictureStainingFields]]:
    logs: list[ExtractLog] = []
    for procedure_xml in xml.xpath("PROCEDURE_INFORMATION"):
        # PROCEDURE_INFORMATION and STAIN(S) are mutually exclusive.
        return _Extracted(
            ids=_object_ids(xml),
            fields=[
                BigpictureStainingFields(
                    staining_procedure=_extract_code_attribute_value(
                        procedure_xml,
                        "staining_procedure",
                        SNOMED_ONTOLOGY_ID,
                        logs,
                        is_attributes=False,
                    ),
                    staining_procedure_other=_extract_string_attribute_value(
                        procedure_xml, "staining_procedure", is_attributes=False
                    ),
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

        fields.append(
            BigpictureStainingFields(
                staining_procedure=_extract_code_attribute_value(
                    stain_xml,
                    "staining_procedure",
                    SNOMED_ONTOLOGY_ID,
                    logs,
                    is_attributes=False,
                ),
                staining_procedure_other=_extract_string_attribute_value(
                    stain_xml, "staining_procedure", is_attributes=False
                ),
                staining_substance=_extract_code_attribute_value(
                    stain_xml,
                    "staining_substance",
                    SNOMED_ONTOLOGY_ID,
                    logs,
                    is_attributes=False,
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

    return _Extracted(ids=_object_ids(xml), fields=fields, logs=logs)


def _extract_diagnoses(
    statement: Element, logs: list[ExtractLog]
) -> set[BigpictureCodeAttributeValue]:
    codes = {
        _code_attribute_value(v)
        for v in statement.xpath("CODE_ATTRIBUTES/CODE_ATTRIBUTE/VALUE")
        if not _is_nil(v)
    }
    return set(_filter_values_by_scheme(codes, SNOMED_ONTOLOGY_ID, "diagnosis", logs))


def _extract_finding(
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
