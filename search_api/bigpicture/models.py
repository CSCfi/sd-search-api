"""Bigpicture Pydantic models."""

import re
from typing import Literal

from pydantic import BaseModel, field_validator


class BigpictureCodeAttributeValue(BaseModel):
    # Example:
    # <CODE_ATTRIBUTE>
    #   <TAG>...</TAG>
    #   <VALUE>
    #     <CODE>9606</CODE>
    #     <SCHEME>NCBI_TAXONOMY</SCHEME>
    #     <MEANING>Homo sapiens</MEANING>
    #     <SCHEME_VERSION>2023</SCHEME_VERSION>
    #    </VALUE>
    #  </CODE_ATTRIBUTE>
    code: str
    scheme: str | None = None
    meaning: str
    scheme_version: str | None = None


_validate_sex_map = {
    "male": "Male",
    "female": "Female",
    "notknown": "Not-known",
    "other": "Other",
}


class BigpictureSampleBiologicalBeingFields(BaseModel):
    species: BigpictureCodeAttributeValue | None
    sex: Literal["Male", "Female", "Not-known", "Other"] | None

    @field_validator("sex", mode="before")
    @classmethod
    def validate_sex(cls, v) -> str | None:
        if not isinstance(v, str):
            return None

        key = re.sub(r"[^a-zA-Z]+", "", v).lower()
        return _validate_sex_map.get(key)


class BigpictureSampleSpecimenFields(BaseModel):
    anatomical_site: BigpictureCodeAttributeValue | None
    fixation_type: BigpictureCodeAttributeValue | None
    specimen_type: BigpictureCodeAttributeValue | None
    age_at_extraction_range: tuple[int, int] | None


class BigpictureSampleBlockFields(BaseModel):
    block_preparation: BigpictureCodeAttributeValue | None


class BigpictureStainingFields(BaseModel):
    pass


class BigpictureFields(BaseModel):
    image_id: str
    dataset_id: str
    dataset_title: str | None
    dataset_description: str | None
    biological_being_fields: list[BigpictureSampleBiologicalBeingFields]
    specimen_fields: list[BigpictureSampleSpecimenFields]
    block_fields: list[BigpictureSampleBlockFields]
    staining_fields: list[BigpictureStainingFields]
