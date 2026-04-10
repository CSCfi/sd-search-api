"""BigPicture Pydantic models."""

from pydantic import BaseModel


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


class BigPictureSampleBiologicalBeingFields(BaseModel):
    species: BigPictureCodeAttributeValue | None
    # TODO(improve): support sex


class BigPictureSampleSpecimenFields(BaseModel):
    anatomical_site: BigPictureCodeAttributeValue | None
    # TODO(improve): support age_at_extraction
    fixation_type: BigPictureCodeAttributeValue | None
    specimen_type: BigPictureCodeAttributeValue | None


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
