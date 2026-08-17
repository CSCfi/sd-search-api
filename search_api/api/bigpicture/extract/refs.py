from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from typing import TypeVar, cast

from lxml.etree import _Element as Element  # noqa

from search_api.api.bigpicture.extract.models import ObjectIds, ObjectKey


_OBSERVATION_IMAGE_REF = "IMAGE_REF"
_OBSERVATION_SLIDE_REF = "SLIDE_REF"
_OBSERVATION_BLOCK_REF = "BLOCK_REF"
_OBSERVATION_SPECIMEN_REF = "SPECIMEN_REF"
_OBSERVATION_BIOLOGICAL_BEING_REF = "BIOLOGICAL_BEING_REF"
_OBSERVATION_CASE_REF = "CASE_REF"


# Maps object's key to the ids of each related object.
type IdMap = dict[ObjectKey, set[ObjectIds]]


# Maps object's key to image accessions if present, else the image aliases.
type ImageIdMap = dict[ObjectKey, set[str]]


RelatedIdType = TypeVar("RelatedIdType", ObjectIds, str)


@dataclass(frozen=True)
class _References:
    image_key_to_image_ids: ImageIdMap = field(default_factory=dict)
    slide_key_to_image_ids: ImageIdMap = field(default_factory=dict)
    block_key_to_slide_ids: IdMap = field(default_factory=dict)
    staining_key_to_slide_ids: IdMap = field(default_factory=dict)
    specimen_key_to_block_ids: IdMap = field(default_factory=dict)
    being_key_to_specimen_ids: IdMap = field(default_factory=dict)
    case_key_to_specimen_ids: IdMap = field(default_factory=dict)

    def image_ids_from_images(self, image_keys: Iterable[ObjectKey]) -> set[str]:
        return _related_ids(image_keys, self.image_key_to_image_ids)

    def image_ids_from_slides(self, slide_keys: Iterable[ObjectKey]) -> set[str]:
        return _related_ids(slide_keys, self.slide_key_to_image_ids)

    def image_ids_from_blocks(self, block_keys: Iterable[ObjectKey]) -> set[str]:
        slide_keys = _related_keys(block_keys, self.block_key_to_slide_ids)
        return self.image_ids_from_slides(slide_keys)

    def image_ids_from_stainings(self, staining_keys: Iterable[ObjectKey]) -> set[str]:
        slide_keys = _related_keys(staining_keys, self.staining_key_to_slide_ids)
        return self.image_ids_from_slides(slide_keys)

    def image_ids_from_specimens(self, specimen_keys: Iterable[ObjectKey]) -> set[str]:
        block_keys = _related_keys(specimen_keys, self.specimen_key_to_block_ids)
        return self.image_ids_from_blocks(block_keys)

    def image_ids_from_beings(self, being_keys: Iterable[ObjectKey]) -> set[str]:
        specimen_keys = _related_keys(being_keys, self.being_key_to_specimen_ids)
        return self.image_ids_from_specimens(specimen_keys)

    def image_ids_from_cases(self, case_keys: Iterable[ObjectKey]) -> set[str]:
        specimen_keys = _related_keys(case_keys, self.case_key_to_specimen_ids)
        return self.image_ids_from_specimens(specimen_keys)


def _related_ids(
    keys: Iterable[ObjectKey], key_to_ids: dict[ObjectKey, set[RelatedIdType]]
) -> set[RelatedIdType]:
    return {related_id for key in keys for related_id in key_to_ids.get(key, set())}


def _related_keys(keys: Iterable[ObjectKey], key_to_ids: IdMap) -> Sequence[ObjectKey]:
    return _object_keys(_related_ids(keys, key_to_ids))


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
    key_to_ids: dict[ObjectKey, set[RelatedIdType]],
    value: RelatedIdType,
) -> None:
    """Map reference aliases and optional accessions to the provided value."""
    for ref in elem.xpath(f"./{ref_tag}"):
        for key in _object_keys(ref):
            key_to_ids.setdefault(key, set()).add(value)


_OBSERVATION_REFS: dict[str, Callable[[_References, Sequence[ObjectKey]], set[str]]] = {
    _OBSERVATION_IMAGE_REF: _References.image_ids_from_images,
    _OBSERVATION_SLIDE_REF: _References.image_ids_from_slides,
    _OBSERVATION_BLOCK_REF: _References.image_ids_from_blocks,
    _OBSERVATION_SPECIMEN_REF: _References.image_ids_from_specimens,
    _OBSERVATION_BIOLOGICAL_BEING_REF: _References.image_ids_from_beings,
    _OBSERVATION_CASE_REF: _References.image_ids_from_cases,
}
