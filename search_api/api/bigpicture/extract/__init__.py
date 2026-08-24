"""Bigpicture XML extraction.

Split by what each part does: `models` holds the parsing models everything else is built
on, `refs` the id graph between the objects, `attributes` the reading of one element's
values, and `document` the orchestration that turns a dataset's files into documents.
"""

from search_api.api.bigpicture.extract.document import (
    extract_dataset_documents,
    extract_documents,
    to_opensearch_values,
)
from search_api.api.bigpicture.extract.models import (
    OBSERVATION_CANDIDATE,
    OBSERVATION_CONFIRMED,
    BigpictureCodeAttributeValue,
    BigpictureFields,
    BigpictureObservationFields,
    BigpictureSpecimenFields,
    BigpictureStainingFields,
    ObjectIds,
    ObjectKey,
)

__all__ = [
    "OBSERVATION_CANDIDATE",
    "OBSERVATION_CONFIRMED",
    "BigpictureCodeAttributeValue",
    "BigpictureFields",
    "BigpictureObservationFields",
    "BigpictureSpecimenFields",
    "BigpictureStainingFields",
    "ObjectIds",
    "ObjectKey",
    "extract_dataset_documents",
    "extract_documents",
    "to_opensearch_values",
]
