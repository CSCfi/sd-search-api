import uuid
from pathlib import Path
import pytest

from search_api.bigpicture.models import (
    BigpictureCodeAttributeValue,
    BigpictureFields,
    BigpictureStainingFields,
    BigpictureBlockFields,
)
from search_api.bigpicture.service import load_fields, get_fields, sync_fields

from search_api.database.repository import get_connection

TEST_DIR = Path(__file__).resolve().parent.parent.parent / "test_files" / "bigpicture"


def get_code(code: str) -> BigpictureCodeAttributeValue:
    return BigpictureCodeAttributeValue(code=code, meaning=code)


@pytest.mark.asyncio
async def test_load_and_sync_fields():
    is_sync_fields = True

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
        dataset_short_name=dataset_short_name,
        dataset_title=dataset_title,
        dataset_description=dataset_description,
        blocks={
            BigpictureBlockFields(
                sex="Male",
                species=get_code("1"),
                anatomical_site=get_code("2"),
                fixation_type=get_code("3"),
                block_preparation=get_code("4"),
                specimen_type=get_code("5"),
                age_at_extraction=(10, 20),
            )
        },
        stains={
            BigpictureStainingFields(
                staining_method="immunostaining",
                staining_procedure=get_code("11"),
                staining_procedure_text="test_procedure",
                staining_compound=get_code("12"),
                staining_compound_text="test_compound",
                staining_target="test_target",
            )
        },
    )

    async with get_connection() as conn:
        async with conn.cursor() as cur:
            await load_fields(cur, fields)

            actual = await get_fields(cur, image_id)

            assert fields.image_id == actual.image_id
            assert fields.dataset_id == actual.dataset_id
            assert fields.dataset_image_cnt == actual.dataset_image_cnt

            assert fields.dataset_short_name == actual.dataset_short_name
            assert fields.dataset_title == actual.dataset_title
            assert fields.dataset_description == actual.dataset_description

            assert len(fields.blocks) == 1
            assert len(fields.stains) == 1

            assert fields.blocks == actual.blocks
            assert fields.stains == actual.stains

            if is_sync_fields:
                await sync_fields(cur, image_id)
