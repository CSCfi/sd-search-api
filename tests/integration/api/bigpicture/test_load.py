import os
import uuid

import pytest

from search_api.api.opensearch.models import ExtractedDocument
from search_api.api.bigpicture.extract import (
    BigpictureCodeAttributeValue,
    BigpictureFields,
    BigpictureStainingFields,
    BigpictureSpecimenFields,
    to_opensearch_values,
)
from search_api.api.bigpicture.domain import BP_DOMAIN
from search_api.services.ontology.term_cache import create_term_caches
from search_api.api.bigpicture.models import BP_OPENSEARCH_INDEX
from search_api.services.sync import SyncService
from search_api.database.document import get_document
from search_api.services.load import LoadService
from search_api.database.repository import get_connection

os.environ.setdefault("POSTGRES_DB", os.environ["BP_POSTGRES_DB"])
os.environ.setdefault("POSTGRES_PORT", os.environ["BP_POSTGRES_PORT"])


def get_code(code: str) -> BigpictureCodeAttributeValue:
    return BigpictureCodeAttributeValue(code=code, meaning=code)


@pytest.mark.asyncio
async def test_load_and_sync_fields():
    image_id = f"image{uuid.uuid4()}"
    dataset_id = f"dataset{uuid.uuid4()}"
    dataset_image_cnt = 1

    dataset_short_name = "test_name"
    dataset_title = "test_title"
    dataset_description = "test_description"

    fields = BigpictureFields(
        image_id=image_id,
        dataset_id=dataset_id,
        dataset_image_cnt=dataset_image_cnt,
        scope="clinical",
        dataset_short_name=dataset_short_name,
        dataset_title=dataset_title,
        dataset_description=dataset_description,
        specimen={
            BigpictureSpecimenFields(
                sex="Male",
                animal_species=get_code("1"),
                anatomical_site=frozenset([get_code("2")]),
                fixation_type=get_code("3"),
                fixation_type_other="test_fixation",
                block_preparation=get_code("4"),
                specimen_type=get_code("5"),
                age_at_extraction=("P10Y", "P20Y"),
            )
        },
        staining={
            BigpictureStainingFields(
                staining_procedure=get_code("11"),
                staining_procedure_other="test_procedure",
                staining_substance=get_code("12"),
                staining_substance_other="test_compound",
                staining_target="test_target",
            )
        },
    )

    sync_service = SyncService(BP_OPENSEARCH_INDEX)
    load_service = LoadService(
        term_caches=create_term_caches(BP_DOMAIN.ontology_ids),
        filtering_terms=BP_DOMAIN.filtering_terms,
        filtering_scopes=BP_DOMAIN.filtering_scopes,
    )

    async with get_connection() as conn:
        async with conn.cursor() as cur:
            values, groups = to_opensearch_values(fields)
            doc = ExtractedDocument(
                id=image_id,
                scope=fields.scope,
                values=values,
                groups=groups,
            )
            await load_service.store_document(cur, doc)

            payload = await get_document(cur, image_id)
            assert payload is not None

            assert payload["scope"] == "clinical"
            assert payload["image_id"] == image_id
            assert payload["dataset_id"] == dataset_id
            assert payload["dataset_image_cnt"] == dataset_image_cnt
            assert payload["dataset_short_name"] == dataset_short_name
            assert payload["dataset_title"] == dataset_title
            assert payload["dataset_description"] == dataset_description
            assert len(payload.get("specimen", [])) == 1
            assert len(payload.get("staining", [])) == 1

            await sync_service.sync_fields(cur, image_id)

    await sync_service.search.close()
