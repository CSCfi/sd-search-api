import uuid
from pathlib import Path
from typing import Collection
import pytest

from search_api.bigpicture.models import (
    BigpictureCodeAttributeValue,
    BigpictureFields,
    BigpictureStainField,
)
from search_api.bigpicture.service import load_fields, get_fields, sync_fields

from search_api.database.repository import get_connection

TEST_DIR = Path(__file__).resolve().parent.parent.parent / "test_files" / "bigpicture"


def get_code(code: str) -> BigpictureCodeAttributeValue:
    return BigpictureCodeAttributeValue(code=code, meaning=code)


def get_codes(codes: set[str]) -> set[BigpictureCodeAttributeValue]:
    s = set()
    for code in codes:
        s.add(get_code(code))
    return s


def assert_codes(
    a: Collection[BigpictureCodeAttributeValue] | None,
    b: Collection[BigpictureCodeAttributeValue] | None,
) -> bool:
    """
    Compare two collections of BigpictureCodeAttributeValue by their code only.
    """

    def _to_code_set(attributes):
        if not attributes:
            return set()
        return {attribute.code for attribute in attributes}

    assert _to_code_set(a) == _to_code_set(b)


@pytest.mark.asyncio
async def test_load_and_sync_fields():
    is_sync_fields = True

    image_id = f"image{uuid.uuid4()}"
    dataset_id = f"dataset{uuid.uuid4()}"
    dataset_image_cnt = 1

    dataset_short_name = "test_name"
    dataset_title = "test_title"
    dataset_description = "test_description"

    sex_values = {"Male", "Female"}

    species_codes = get_codes({"1", "2"})
    anatomical_site_codes = get_codes({"3", "4"})
    fixation_type_codes = get_codes({"5", "6"})
    block_preparation_codes = get_codes({"7", "8"})
    specimen_type_codes = get_codes({"9", "10"})
    age_at_extraction_ranges = {(10, 20), (30, 40)}

    fields = BigpictureFields(
        image_id=image_id,
        dataset_id=dataset_id,
        dataset_image_cnt=dataset_image_cnt,
        dataset_short_name=dataset_short_name,
        dataset_title=dataset_title,
        dataset_description=dataset_description,
        sex=sex_values,
        species=species_codes,
        anatomical_site=anatomical_site_codes,
        fixation_type=fixation_type_codes,
        block_preparation=block_preparation_codes,
        specimen_type=specimen_type_codes,
        age_at_extraction=age_at_extraction_ranges,
        stains={
            BigpictureStainField(
                staining_method="immunostaining",
                staining_procedure=get_code("11"),
                staining_procedure_text="test_procedure",
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

            assert fields.sex == actual.sex
            assert_codes(fields.species, actual.species)
            assert_codes(fields.anatomical_site, actual.anatomical_site)
            assert_codes(fields.fixation_type, actual.fixation_type)
            assert_codes(fields.block_preparation, actual.block_preparation)
            assert_codes(fields.specimen_type, actual.specimen_type)
            assert fields.age_at_extraction, actual.age_at_extraction
            assert fields.stains == actual.stains

            if is_sync_fields:
                await sync_fields(cur, image_id)
