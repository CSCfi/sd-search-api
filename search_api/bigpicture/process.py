from pathlib import Path
from typing import Iterator
from lxml.etree import _ElementTree as ElementTree  # noqa

import fsspec  # type: ignore

from search_api.bigpicture.models import (
    BigPictureFields,
    BigPictureCodeAttributeValue,
    BigPictureSampleBiologicalBeingFields,
    BigPictureSampleSpecimenFields,
    BigPictureSampleBlockFields,
    BigPictureStainingFields,
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


def process_directories(
    root: str = "/",
    fs: fsspec.AbstractFileSystem | None = None,
    use_aliases: bool = False,
) -> Iterator[BigPictureFields]:
    """
    Process directories under a root path.

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
            map_specimen_to_block_ids: dict[str, set[str]] = {}
            map_case_to_specimen_ids: dict[str, set[str]] = {}
            map_biological_being_to_case_ids: dict[str, set[str]] = {}
            map_biological_being_to_specimen_ids: dict[str, set[str]] = {}

            def add_slide(_slide_id: str, _image_id: str) -> None:
                map_slide_to_image_ids.setdefault(_slide_id, set()).add(_image_id)

            def get_image_ids_for_slide_id(_slide_id: str) -> set[str]:
                return map_slide_to_image_ids.get(_slide_id, set())

            def add_block(_block_id: str, _slide_id: str) -> None:
                map_block_to_slide_ids.setdefault(_block_id, set()).add(_slide_id)

            def get_image_ids_for_block_id(_block_id: str) -> set[str]:
                _image_ids = set()
                for _slide_id in map_block_to_slide_ids.get(_block_id, []):
                    _image_ids.update(get_image_ids_for_slide_id(_slide_id))
                return _image_ids

            def add_specimen(_specimen_id: str, _block_id: str) -> None:
                map_specimen_to_block_ids.setdefault(_specimen_id, set()).add(_block_id)

            def get_image_ids_for_specimen_id(_specimen_id: str) -> set[str]:
                _image_ids = set()
                for _block_id in map_specimen_to_block_ids.get(_specimen_id, []):
                    _image_ids.update(get_image_ids_for_block_id(_block_id))
                return _image_ids

            def add_case(_case_id: str, _specimen_id: str) -> None:
                map_case_to_specimen_ids.setdefault(_case_id, set()).add(_specimen_id)

            def add_biological_being_to_specimen(
                _biological_being_id: str, _specimen_id: str
            ):
                map_biological_being_to_specimen_ids.setdefault(
                    _biological_being_id, set()
                ).add(_specimen_id)

            def get_image_ids_for_case_id(_case_id: str) -> set[str]:
                _image_ids = set()
                for _specimen_id in map_case_to_specimen_ids.get(_case_id, []):
                    _image_ids.update(get_image_ids_for_specimen_id(_specimen_id))
                return _image_ids

            def add_biological_being_to_case(_biological_being_id: str, _case_id: str):
                map_biological_being_to_case_ids.setdefault(
                    _biological_being_id, set()
                ).add(_case_id)

            def get_image_ids_for_biological_being_id(
                _biological_being_id: str,
            ) -> set[str]:
                _image_ids = set()
                for _case_id in map_biological_being_to_case_ids.get(
                    _biological_being_id, []
                ):
                    _image_ids.update(get_image_ids_for_case_id(_case_id))
                for _specimen_id in map_biological_being_to_specimen_ids.get(
                    _biological_being_id, []
                ):
                    _image_ids.update(get_image_ids_for_specimen_id(_specimen_id))
                return _image_ids

            # Map image ids to search fields.
            image_id_to_sample_biological_being_fields: dict[
                str, list[BigPictureSampleBiologicalBeingFields]
            ] = {}
            image_id_to_sample_specimen_fields: dict[
                str, list[BigPictureSampleSpecimenFields]
            ] = {}
            image_id_to_sample_block_fields: dict[
                str, list[BigPictureSampleBlockFields]
            ] = {}
            image_id_to_staining_fields: dict[str, list[BigPictureStainingFields]] = {}

            def add_search_fields(
                _image_id: str,
                _fields: BigPictureSampleBiologicalBeingFields
                | BigPictureSampleSpecimenFields
                | BigPictureSampleBlockFields
                | BigPictureStainingFields,
            ) -> None:
                if isinstance(_fields, BigPictureSampleBiologicalBeingFields):
                    image_id_to_sample_biological_being_fields.setdefault(
                        image_id, []
                    ).append(_fields)
                elif isinstance(_fields, BigPictureSampleSpecimenFields):
                    image_id_to_sample_specimen_fields.setdefault(image_id, []).append(
                        _fields
                    )
                elif isinstance(_fields, BigPictureSampleBlockFields):
                    image_id_to_sample_block_fields.setdefault(image_id, []).append(
                        _fields
                    )
                elif isinstance(_fields, BigPictureStainingFields):
                    image_id_to_staining_fields.setdefault(image_id, []).append(_fields)
                else:
                    raise ValueError("Unsupported search fields type")

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
                dataset_title = get_xml_value(
                    "/DATASET/TITLE | /DATASET_SET/DATASET/TITLE", dataset_xml
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
                        add_slide(slide_id, image_id)

            # Read sample XML.
            #

            with fs.open(sample_file_path, "rb") as f:
                sample_xml = parse_xml(f.read())
                validate_xml(sample_xml, XML_SCHEMA_DIR, SAMPLE_XML_SCHEMA_FILE)

                for xml in sample_xml.xpath("/SLIDE | /SAMPLE_SET/SLIDE"):
                    slide_id = xml.get(id_attribute)
                    for block_id in xml.xpath(f"./CREATED_FROM_REF/@{id_attribute}"):
                        add_block(block_id, slide_id)

                for xml in sample_xml.xpath("/BLOCK | /SAMPLE_SET/BLOCK"):
                    block_id = xml.get(id_attribute)
                    for specimen_id in xml.xpath(f"./SAMPLED_FROM_REF/@{id_attribute}"):
                        add_specimen(specimen_id, block_id)

                for xml in sample_xml.xpath("/SPECIMEN | /SAMPLE_SET/SPECIMEN"):
                    specimen_id = xml.get(id_attribute)
                    for case_id in xml.xpath(f"./PART_OF_CASE_REF/@{id_attribute}"):
                        add_case(case_id, specimen_id)
                    for biological_being_id in xml.xpath(
                        f"./EXTRACTED_FROM_REF/@{id_attribute}"
                    ):
                        add_biological_being_to_specimen(
                            biological_being_id, specimen_id
                        )

                for xml in sample_xml.xpath("/CASE | /SAMPLE_SET/CASE"):
                    case_id = xml.get(id_attribute)
                    for biological_being_id in xml.xpath(
                        f"./BIOLOGICAL_BEING_REF/@{id_attribute}"
                    ):
                        add_biological_being_to_case(biological_being_id, case_id)

                # Create BigPictureSampleBiologicalBeingFields
                for xml in sample_xml.xpath(
                    "/BIOLOGICAL_BEING | /SAMPLE_SET/BIOLOGICAL_BEING"
                ):
                    for image_id in get_image_ids_for_biological_being_id(
                        xml.get(id_attribute)
                    ):
                        add_search_fields(
                            image_id,
                            BigPictureSampleBiologicalBeingFields(
                                species=_get_code_attribute_value(
                                    xml, "animal_species"
                                ),
                                # TODO(improve): support 'sex'
                            ),
                        )

                # Create BigPictureSampleSpecimenFields
                for xml in sample_xml.xpath("/SPECIMEN | /SAMPLE_SET/SPECIMEN"):
                    for image_id in get_image_ids_for_specimen_id(
                        xml.get(id_attribute)
                    ):
                        add_search_fields(
                            image_id,
                            BigPictureSampleSpecimenFields(
                                anatomical_site=_get_code_attribute_value(
                                    xml, "anatomical_site"
                                ),
                                fixation_type=_get_code_attribute_value(
                                    xml, "fixation_type"
                                ),
                                specimen_type=_get_code_attribute_value(
                                    xml, "specimen_type"
                                ),
                            ),
                        )

                # Create BigPictureSampleBlockFields
                for xml in sample_xml.xpath("/BLOCK | /SAMPLE_SET/BLOCK"):
                    for image_id in get_image_ids_for_block_id(xml.get(id_attribute)):
                        add_search_fields(
                            image_id,
                            BigPictureSampleBlockFields(
                                block_preparation=_get_code_attribute_value(
                                    xml, "block_preparation"
                                )
                            ),
                        )

                # Read staining XML.
                #

                with fs.open(staining_file_path, "rb") as f:
                    staining_xml = parse_xml(f.read())
                validate_xml(staining_xml, XML_SCHEMA_DIR, STAINING_XML_SCHEMA_FILE)

                for xml in staining_xml.xpath("/STAINING | /STAINING_SET/STAINING"):
                    # TODO(improve): parse staining XML
                    pass

                #
                #

                for image_id in image_ids:
                    yield BigPictureFields(
                        image_id=image_id,
                        dataset_id=dataset_id,
                        dataset_title=dataset_title,
                        dataset_description=dataset_description,
                        biological_being_fields=image_id_to_sample_biological_being_fields.get(
                            image_id, []
                        ),
                        specimen_fields=image_id_to_sample_specimen_fields.get(
                            image_id, []
                        ),
                        block_fields=image_id_to_sample_block_fields.get(image_id, []),
                        staining_fields=[],  # TODO(improve): add staining fields
                    )

        except Exception as e:
            raise e


def _get_code_attribute_value(
    elem: ElementTree, tag: str
) -> BigPictureCodeAttributeValue | None:
    values = elem.xpath(f"//ATTRIBUTES/CODE_ATTRIBUTE[TAG='{tag}']/VALUE")
    if not values:
        return None
    value = values[0]

    return BigPictureCodeAttributeValue(
        code=value.findtext("CODE"),
        scheme=value.findtext("SCHEME"),
        meaning=value.findtext("MEANING"),
        scheme_version=value.findtext("SCHEME_VERSION"),
    )
