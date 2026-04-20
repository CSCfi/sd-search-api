"""Bigpicture models."""

from typing import Literal

from pydantic import BaseModel, ConfigDict


class BigpictureCodeAttributeValue(BaseModel):
    """Bigpicture code attribute value."""

    model_config = ConfigDict(frozen=True)

    code: str
    scheme: str | None = None
    meaning: str
    scheme_version: str | None = None


class BigpictureSampleBiologicalBeingFields(BaseModel):
    """Bigpicture biological being search fields."""

    species: set[BigpictureCodeAttributeValue] = set()
    sex: set[Literal["Male", "Female", "Not-known", "Other"]] = set()


class BigpictureSampleSpecimenFields(BaseModel):
    """Bigpicture specimen search fields."""

    anatomical_site: set[BigpictureCodeAttributeValue] = set()
    fixation_type: set[BigpictureCodeAttributeValue] = set()
    specimen_type: set[BigpictureCodeAttributeValue] = set()
    age_at_extraction: set[tuple[int, int]] = set()


class BigpictureSampleBlockFields(BaseModel):
    """Bigpicture block search fields."""

    block_preparation: set[BigpictureCodeAttributeValue] = set()


class BigpictureStainingFields(BaseModel):
    """Bigpicture staining search fields."""

    pass


class BigpictureFields(
    BigpictureSampleBiologicalBeingFields,
    BigpictureSampleSpecimenFields,
    BigpictureSampleBlockFields,
    BigpictureStainingFields,
    BaseModel,
):
    """Bigpicture IDs and search fields."""

    image_id: str
    dataset_id: str
    dataset_image_cnt: int
    dataset_short_name: str | None = None
    dataset_title: str | None = None
    dataset_description: str | None = None
