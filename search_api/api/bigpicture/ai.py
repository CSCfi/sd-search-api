"""Bigpicture-specific AI search result shape and prompt instructions."""

from pydantic import BaseModel

from search_api.ai.models import AISearchResult


class BigpictureAISearchResult(AISearchResult):
    """AI search result grouped by Bigpicture dataset."""

    class Dataset(BaseModel):
        dataset_id: str
        dataset_title: str | None = None
        matching_image_count: int
        total_image_count: int

    dataset_count: int
    datasets: list[Dataset]


# The AI agent persona.
BP_AI_ASSISTANT_DESCRIPTION = (
    "a biomedical image search assistant for the Bigpicture digital pathology dataset"
)

# Bigpicture-specific result fields, appended to the generic result instructions.
BP_AI_RESULT_INSTRUCTIONS = """\
   - dataset_count: number of datasets in the results
   - datasets: one entry per dataset in the results"""
