"""BigPicture Pydantic models."""

import re
from typing import Literal

from pydantic import BaseModel, field_validator


class BigPictureCodeAttributeValue(BaseModel):
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


class BigPictureSampleBiologicalBeingFields(BaseModel):
    species: BigPictureCodeAttributeValue | None
    sex: Literal["Male", "Female", "Not-known", "Other"] | None

    @field_validator("sex", mode="before")
    @classmethod
    def validate_sex(cls, v) -> str | None:
        if not isinstance(v, str):
            return None

        key = re.sub(r"[^a-zA-Z]+", "", v).lower()
        return _validate_sex_map.get(key)


class BigPictureSampleSpecimenFields(BaseModel):
    anatomical_site: BigPictureCodeAttributeValue | None
    fixation_type: BigPictureCodeAttributeValue | None
    specimen_type: BigPictureCodeAttributeValue | None
    age_at_extraction_range: tuple[int, int] | None


class BigPictureSampleBlockFields(BaseModel):
    block_preparation: BigPictureCodeAttributeValue | None


class BigPictureStainingFields(BaseModel):
    pass


class BigPictureFields(BaseModel):
    image_id: str
    dataset_id: str
    dataset_title: str | None
    dataset_description: str | None
    biological_being_fields: list[BigPictureSampleBiologicalBeingFields]
    specimen_fields: list[BigPictureSampleSpecimenFields]
    block_fields: list[BigPictureSampleBlockFields]
    staining_fields: list[BigPictureStainingFields]
