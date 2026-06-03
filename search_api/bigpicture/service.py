"""Bigpicture services."""

import logging
from typing import Any, Collection

from psycopg import AsyncCursor
from psycopg.types.json import Json

from search_api.bigpicture.models import (
    BigpictureFields,
    BigpictureCodeAttributeValue,
    BigpictureStainingFields,
    BigpictureBlockFields,
)
from search_api.database.repository import get_cursor
from search_api.services.search import bp_index_documents

logging.basicConfig(level=logging.INFO)

# OpenSearch index batch size.
BATCH_SIZE = 1000


async def load_fields(cur: AsyncCursor, fields: BigpictureFields) -> None:
    """
    Load Bigpicture fields for one image into the database.

    :param cur: The database cursor.
    :param fields: The Bigpicture fields for one image.
    """

    await _load_fields(
        cur,
        fields.image_id,
        fields.dataset_id,
        fields.dataset_image_cnt,
        fields.dataset_short_name,
        fields.dataset_title,
        fields.dataset_description,
        fields.blocks,
        fields.stains,
    )


async def _load_fields(
    cur: AsyncCursor,
    image_id: str,
    dataset_id: str,
    dataset_image_cnt: int,
    dataset_short_name: str | None,
    dataset_title: str | None,
    dataset_description: str | None,
    blocks: Collection[BigpictureBlockFields] | None,
    stains: Collection[BigpictureStainingFields] | None,
) -> None:
    """
    Load Bigpicture fields for one image into the database.

    :param cur: The database cursor.
    :param image_id: Unique identifier of the image.
    :param dataset_id: Unique Identifier of the dataset the image belongs to.
    :param dataset_image_cnt Number of images in the dataset.
    :param dataset_short_name Short name of the dataset.
    :param dataset_title: Title of the dataset.
    :param dataset_description: Description of the dataset.
    :param blocks: List of blocks.
    :param stains: List of stains.
    """

    def _extract_code(value: BigpictureCodeAttributeValue | None) -> str | None:
        return value.code if value is not None else None

    # Replace existing image row.
    await cur.execute(
        """
        INSERT INTO bp_image (
            image_id,
            dataset_id,
            dataset_image_cnt,
            dataset_short_name,
            dataset_title,
            dataset_description,
            blocks,
            stains
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (image_id) DO UPDATE
        SET
            dataset_id = EXCLUDED.dataset_id,
            dataset_image_cnt = EXCLUDED.dataset_image_cnt,
            dataset_short_name = EXCLUDED.dataset_short_name,
            dataset_title = EXCLUDED.dataset_title,
            dataset_description = EXCLUDED.dataset_description,
            blocks = EXCLUDED.blocks,
            stains = EXCLUDED.stains
        """,
        (
            image_id,
            dataset_id,
            dataset_image_cnt,
            dataset_short_name,
            dataset_title,
            dataset_description,
            # blocks
            Json(
                [
                    b
                    for b in [
                        {
                            k: v
                            for k, v in {
                                "block_preparation": _extract_code(
                                    block.block_preparation
                                ),
                                "anatomical_site": _extract_code(block.anatomical_site),
                                "fixation_type": _extract_code(block.fixation_type),
                                "specimen_type": _extract_code(block.specimen_type),
                                "age_at_extraction": (
                                    {
                                        "gte": block.age_at_extraction[0],
                                        "lte": block.age_at_extraction[1],
                                    }
                                    if block.age_at_extraction is not None
                                    else None
                                ),
                                "species": _extract_code(block.species),
                                "sex": block.sex,
                            }.items()
                            if v is not None
                        }
                        for block in blocks
                    ]
                    if b
                ]
            )
            if blocks
            else None,
            # stains
            Json(
                [
                    s
                    for s in [
                        {
                            k: v
                            for k, v in {
                                "staining_target": stain.staining_target,
                                "staining_procedure": _extract_code(
                                    stain.staining_procedure
                                ),
                                "staining_procedure_text": stain.staining_procedure_text,
                                "staining_compound": _extract_code(
                                    stain.staining_compound
                                ),
                                "staining_compound_text": stain.staining_compound_text,
                            }.items()
                            if v is not None
                        }
                        for stain in stains
                    ]
                    if s
                ]
            )
            if stains
            else None,
        ),
    )


async def get_fields(cur: AsyncCursor, image_id: str) -> BigpictureFields | None:
    """
    Get Bigpicture fields for one image from the database. For ontology fields columns
    uses the code value also for the meaning.

    :param cur: The database cursor.
    :param image_id: Unique identifier of the image.
    :return: The Bigpicture fields for the image.
    """

    await cur.execute(
        """
        SELECT
            image_id,
            dataset_id,
            dataset_image_cnt,
            dataset_short_name,
            dataset_title,
            dataset_description,
            blocks,
            stains
        FROM bp_image
        WHERE image_id = %s
        """,
        (image_id,),
    )

    row = await cur.fetchone()
    if not row:
        return None

    (
        image_id,
        dataset_id,
        dataset_image_cnt,
        dataset_short_name,
        dataset_title,
        dataset_description,
        blocks,
        stains,
    ) = row

    def _convert_code(key: str, _dict: dict) -> dict:
        v = _dict.get(key)
        if v is None:
            return {}
        return {key: BigpictureCodeAttributeValue(code=v, meaning=v)}

    def _convert_age_at_extraction(_dict: dict) -> dict:
        v = _dict.get("age_at_extraction")
        if v is None:
            return {}
        return {"age_at_extraction": (v["gte"], v["lte"])}

    blocks = {
        BigpictureBlockFields(
            **{
                **block,
                **_convert_code("block_preparation", block),
                **_convert_code("species", block),
                **_convert_code("anatomical_site", block),
                **_convert_code("fixation_type", block),
                **_convert_code("specimen_type", block),
                **_convert_age_at_extraction(block),
            }
        )
        for block in (blocks or [])
    }

    stains = {
        BigpictureStainingFields(
            **{
                **stain,
                **_convert_code("staining_procedure", stain),
                **_convert_code("staining_compound", stain),
            }
        )
        for stain in (stains or [])
    }

    return BigpictureFields(
        image_id=image_id,
        dataset_id=dataset_id,
        dataset_image_cnt=dataset_image_cnt,
        dataset_short_name=dataset_short_name,
        dataset_title=dataset_title,
        dataset_description=dataset_description,
        blocks=blocks,
        stains=stains,
    )


async def sync_fields(cur: AsyncCursor, image_id: str | None = None) -> None:
    """
    Sync database fields to OpenSearch.

    :param cur: The database cursor.
    :param image_id: An optional image id.
    """

    # Find imaged to sync to OpenSearch.

    logging.info("Finding images to sync to OpenSearch.")

    ids_batch: list[str] = []
    docs_batch: list[dict[str, Any]] = []

    async with get_cursor() as update_cur:
        query = """
            SELECT
                image_id,
                dataset_id,
                dataset_image_cnt,
                dataset_short_name,
                dataset_title,
                dataset_description,
                blocks,
                stains
            FROM bp_image
            WHERE search_sync = false
        """

        params = []

        if image_id is not None:
            query += " AND image_id = %s"
            params.append(image_id)

        await cur.execute(query, params)

        async def flush_batch():
            # Flush the OpenSearch batch.
            logging.info(f"Syncing {len(docs_batch)} images to OpenSearch.")
            await bp_index_documents(ids_batch, docs_batch)

            # Update OpenSearch state in database.
            logging.info("Updating sync status.")
            await update_cur.executemany(
                """
                UPDATE bp_image
                SET
                    search_sync = true,
                    search_sync_date = now()
                WHERE image_id = %s
                """,
                [(i,) for i in ids_batch],
            )

            # Clear the OpenSearch batch.
            ids_batch.clear()
            docs_batch.clear()

        async for row in cur:
            (
                image_id,
                dataset_id,
                dataset_image_cnt,
                dataset_short_name,
                dataset_title,
                dataset_description,
                blocks,
                stains,
            ) = row

            # Create OpenSearch document for indexing.

            doc = {
                "image_id": image_id,
                "dataset_id": dataset_id,
                "dataset_image_cnt": dataset_image_cnt,
                "dataset_short_name": dataset_short_name,
                "dataset_title": dataset_title,
                "dataset_description": dataset_description,
                "blocks": blocks,
                "stains": stains,
            }

            # Add to the OpenSearch batch.
            ids_batch.append(image_id)
            docs_batch.append(doc)

            if len(docs_batch) >= BATCH_SIZE:
                # Flush batch.
                await flush_batch()

        if ids_batch:
            # Flush final batch.
            await flush_batch()


async def sync_count(cur: AsyncCursor) -> int:
    """
    Return number of images to sync to OpenSearch.

    :param cur: The database cursor.
    :return: THe number of images to sync to OpenSearch.
    """

    # Find imaged to sync to OpenSearch.

    await cur.execute("""
        SELECT COUNT(1)
        FROM bp_image
        WHERE search_sync = false
    """)

    return (await cur.fetchone())[0]  # type: ignore
