"""Bigpicture-specific AI search result shape and prompt instructions."""

from pydantic import BaseModel

from search_api.ai.models import AISearchResult


class BigpictureAIDatasetSearchResult(AISearchResult):
    """AI search result grouped by Bigpicture dataset."""

    class Dataset(BaseModel):
        dataset_id: str
        dataset_title: str | None = None
        matching_image_count: int
        total_image_count: int

    dataset_count: int
    datasets: list[Dataset]


class BigpictureAIImageSearchResult(AISearchResult):
    """AI search result of individual Bigpicture images."""

    class Image(BaseModel):
        image_id: str

    image_count: int
    images: list[Image]


# The AI agent persona, shared by the /ai/datasets and /ai/images agents.
BP_AI_ASSISTANT_DESCRIPTION = (
    "a biomedical image search assistant for the Bigpicture digital pathology dataset"
)

# Bigpicture-specific result fields, appended to the generic result instructions.
BP_AI_DATASET_RESULT_INSTRUCTIONS = """\
   - dataset_count: number of datasets in the results
   - datasets: one entry per dataset in the results"""

BP_AI_IMAGE_RESULT_INSTRUCTIONS = """\
   - image_count: number of images in the results
   - images: one entry per image in the results"""
