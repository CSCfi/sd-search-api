"""Bigpicture models."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_serializer


class BigpictureCodeAttributeValue(BaseModel):
    """Bigpicture code attribute value."""

    model_config = ConfigDict(frozen=True)

    code: str
    scheme: str | None = None
    meaning: str
    scheme_version: str | None = None


class BigpictureSampleBiologicalBeingFields(BaseModel):
    """Bigpicture biological being search fields."""

    species: BigpictureCodeAttributeValue | None = None
    sex: Literal["Male", "Female", "Not-known", "Other"] | None = None


class BigpictureSampleSpecimenFields(BaseModel):
    """Bigpicture specimen search fields."""

    anatomical_site: frozenset[BigpictureCodeAttributeValue] = Field(
        default_factory=frozenset
    )
    fixation_type: BigpictureCodeAttributeValue | None = None
    fixation_type_text: str | None = None  # Free text alternative
    specimen_type: BigpictureCodeAttributeValue | None = None
    age_at_extraction: tuple[str, str] | None = None

    @field_serializer("anatomical_site")
    def _serialize_anatomical_site(
        self, v: frozenset[BigpictureCodeAttributeValue]
    ) -> list[dict]:
        # BigpictureFields.blocks is defined as set[BigpictureBlockFields], and
        # Python requires set elements to be hashable. By default, Pydantic serialises
        # frozenset[BaseModel] as set[dict]. However, dict, unlike list, is unhashable.
        return [item.model_dump() for item in v]


class BigpictureSampleBlockFields(BaseModel):
    """Bigpicture block search fields."""

    block_preparation: BigpictureCodeAttributeValue | None = None


class BigpictureBlockFields(
    BigpictureSampleBiologicalBeingFields,
    BigpictureSampleSpecimenFields,
    BigpictureSampleBlockFields,
    BaseModel,
):
    """Bigpicture block search field."""

    model_config = ConfigDict(frozen=True)


class BigpictureAggregatedBlockFields(BaseModel):
    """Bigpicture block search fields."""

    blocks: set[BigpictureBlockFields] = set()


class BigpictureStainingFields(BaseModel):
    """Bigpicture staining search field."""

    model_config = ConfigDict(frozen=True)

    staining_procedure: BigpictureCodeAttributeValue | None = None
    staining_procedure_text: str | None = None  # Free text alternative
    staining_substance: BigpictureCodeAttributeValue | None = None
    staining_substance_text: str | None = None  # Free text alternative
    staining_target: str | None = None


class BigpictureAggregatedStainingFields(BaseModel):
    """Bigpicture staining search fields."""

    stains: set[BigpictureStainingFields] = set()


class BigpictureFields(
    BigpictureAggregatedBlockFields,
    BigpictureAggregatedStainingFields,
    BaseModel,
):
    """Bigpicture IDs and search fields."""

    image_id: str
    dataset_id: str
    dataset_image_cnt: int
    dataset_short_name: str | None = None
    dataset_title: str | None = None
    dataset_description: str | None = None
    dataset_modified_at: datetime | None = (
        None  # newest file modification date in the dataset
    )
