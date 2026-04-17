"""Bigpicture services."""

import logging
from typing import Any

from psycopg import AsyncCursor
from psycopg.types.range import Range

from search_api.bigpicture.models import BigpictureFields, BigpictureCodeAttributeValue
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

    def _get_codes(
            items: set[BigpictureCodeAttributeValue] | None,
    ) -> list[str] | None:
        if not items:
            return None
        return [item.code for item in items if item is not None]

    # Get ontology codes.
    species_codes = _get_codes(fields.species)
    anatomical_site_codes = _get_codes(fields.anatomical_site)
    fixation_type_codes = _get_codes(fields.fixation_type)
    specimen_type_codes = _get_codes(fields.specimen_type)
    block_preparation_codes = _get_codes(fields.block_preparation)

    # Get controlled values.
    sex_values = list(fields.sex)

    # Get age at extraction values.
    age_at_extraction_values = []
    for value in fields.age_at_extraction:
        if not value:
            continue
        start, end = value
        age_at_extraction_values.append((start, end))

    await _load_fields(
        cur,
        fields.image_id,
        fields.dataset_id,
        fields.dataset_description,
        species_codes,
        anatomical_site_codes,
        sex_values,  # type: ignore
        fixation_type_codes,
        specimen_type_codes,
        block_preparation_codes,
        age_at_extraction_values,
    )


async def _load_fields(
        cur: AsyncCursor,
        image_id: str,
        dataset_id: str,
        dataset_description: str | None,
        species_codes: list[str] | None,
        anatomical_site_codes: list[str] | None,
        sex_values: list[str] | None,
        fixation_type_codes: list[str] | None,
        specimen_type_codes: list[str] | None,
        block_preparation_codes: list[str] | None,
        age_at_extraction_ranges: list[tuple[int, int]] | None,
) -> None:
    """
    Load Bigpicture fields for one image into the database.

    :param cur: The database cursor.
    :param image_id: Unique identifier of the image.
    :param dataset_id: Unique Identifier of the dataset the image belongs to.
    :param dataset_description: Description of the dataset.
    :param species_codes: List of species codes.
    :param anatomical_site_codes: List of anatomical site codes.
    :param sex_values: List of sex values (Male, Female, Not-known, Other).
    :param fixation_type_codes: List of fixation type codes.
    :param specimen_type_codes: List of specimen type codes.
    :param block_preparation_codes: List of block preparation codes.
    :param age_at_extraction_ranges: List of ages at extraction ranges.
    """

    # Replace existing row the image.
    await cur.execute(
        """
        INSERT INTO bp_image (
            image_id,
            dataset_id,
            dataset_description,
            species,
            anatomical_site,
            sex,
            fixation_type,
            specimen_type,
            block_preparation
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (image_id) DO UPDATE
        SET
            dataset_id = EXCLUDED.dataset_id,
            dataset_description = EXCLUDED.dataset_description,
            species = EXCLUDED.species,
            anatomical_site = EXCLUDED.anatomical_site,
            sex = EXCLUDED.sex,
            fixation_type = EXCLUDED.fixation_type,
            specimen_type = EXCLUDED.specimen_type,
            block_preparation = EXCLUDED.block_preparation
        """,
        (
            image_id,
            dataset_id,
            dataset_description,  # text into GIN indexes TEXT field
            species_codes,  # codes into GIN indexed TEXT[] field
            anatomical_site_codes,  # codes into GIN indexed TEXT[] field
            sex_values,  # values into GIN indexed TEXT[] field
            fixation_type_codes,  # codes into GIN indexed TEXT[] field
            specimen_type_codes,  # codes into GIN indexed TEXT[] field
            block_preparation_codes,  # codes into GIN indexed TEXT[] field
        ),
    )

    if age_at_extraction_ranges:
        for age_at_extraction_range in age_at_extraction_ranges:
            # Delete existing rows for the image.
            await cur.execute(
                """
                DELETE FROM bp_image_extraction
                WHERE image_id = %s
                """,
                (image_id,),
            )

            await cur.execute(
                """
                INSERT INTO bp_image_extraction (
                    image_id,
                    age_at_extraction
                )
                VALUES (%s, %s)
                """,
                (
                    image_id,
                    Range(age_at_extraction_range[0], age_at_extraction_range[1], bounds="[]")
                    if age_at_extraction_range
                    else None,  # int range into GIST indexed int4range field
                ),
            )


async def sync_fields(cur: AsyncCursor) -> None:
    """
    Sync database fields to OpenSearch.

    :param cur: The database cursor.
    """

    # Find imaged to sync to OpenSearch.

    logging.info("Finding images to sync to OpenSearch.")

    ids_batch: list[str] = []
    docs_batch: list[dict[str, Any]] = []

    async with get_cursor() as update_cur:
        await cur.execute("""
            SELECT
                bp_image.image_id,
                bp_image.dataset_id,
                bp_image.dataset_description,
                bp_image.species,
                bp_image.anatomical_site,
                bp_image.sex,
                bp_image.fixation_type,
                bp_image.block_preparation,
                bp_image.specimen_type,
                bp_image_extraction.age_at_extraction
            FROM bp_image
            LEFT JOIN bp_image_extraction USING (image_id)
            WHERE bp_image.search_sync = false
        """)

        async def flush_batch():
            # Flush the OpenSearch batch.
            logging.info(f"Syncing {len(docs_batch)} images to OpenSearch.")
            await bp_index_documents(ids_batch, docs_batch)

            # Update OpenSearch state in database.
            logging.info(f"Updating sync status.")
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
                dataset_description,
                species,
                anatomical_site,
                sex,
                fixation_type,
                block_preparation,
                specimen_type,
                age_at_extraction,
            ) = row

            # Create OpenSearch document for indexing.

            doc = {
                "image_id": image_id,
                "dataset_id": dataset_id,
                "dataset_description": dataset_description,
                "species": species or [],
                "anatomical_site": anatomical_site or [],
                "sex": sex or [],
                "fixation_type": fixation_type or [],
                "block_preparation": block_preparation or [],
                "specimen_type": specimen_type or [],
            }

            if age_at_extraction is not None:
                doc["age_at_extraction"] = {
                    "gte": age_at_extraction.lower,
                    "lte": age_at_extraction.upper,
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
        WHERE bp_image.search_sync = false
    """)
    return (await cur.fetchone())[0]
