"""Bigpicture services."""

import logging
from typing import Any

from psycopg import AsyncCursor
from psycopg.types.json import Json

from search_api.bigpicture.models import (
    BigpictureFields,
    BigpictureCodeAttributeValue,
    BigpictureStainingFields,
    BigpictureBlockFields,
)
from search_api.database.repository import get_cursor
import isodate  # type: ignore[import-untyped]

from search_api.api.bigpicture.models import BP_OPENSEARCH_INDEX
from opensearchpy import AsyncOpenSearch

from search_api.api.opensearch.services import (
    create_search,
    index_documents,
    iso8601_duration_to_days,
)

logging.basicConfig(level=logging.INFO)

# OpenSearch index batch size.
BATCH_SIZE = 1000


def _convert_iso8601_range_for_opensearch(range: dict) -> dict | None:
    """Convert an ISO-8601 duration range dict to days (long) for OpenSearch.

    Returns the converted dict, or ``None`` if either bound is missing or invalid.
    """
    if "gte" not in range or "lte" not in range:
        logging.error(
            "age_at_extraction range %r is missing 'gte' or 'lte'; skipping field.",
            range,
        )
        return None
    try:
        return {
            "gte": iso8601_duration_to_days(range["gte"]),
            "lte": iso8601_duration_to_days(range["lte"]),
        }
    except isodate.ISO8601Error:
        logging.error(
            "Invalid ISO-8601 duration in age_at_extraction %r; skipping field.",
            range,
        )
        return None


def _convert_blocks_for_opensearch(blocks: list[dict] | None) -> list[dict] | None:
    """Convert blocks fields for OpenSearch."""
    if not blocks:
        return blocks
    result = []
    for block in blocks:
        age = block.get("age_at_extraction")
        if age:
            converted = _convert_iso8601_range_for_opensearch(age)
            if converted is not None:
                block = {**block, "age_at_extraction": converted}
            else:
                block = {k: v for k, v in block.items() if k != "age_at_extraction"}
        result.append(block)
    return result


class BigpictureService:
    def __init__(self) -> None:
        self._search = create_search()

    @property
    def search(self) -> AsyncOpenSearch:
        return self._search

    @staticmethod
    async def load_fields(cur: AsyncCursor, fields: BigpictureFields) -> None:
        """
        Load Bigpicture fields for one image into the database.

        :param cur: The database cursor.
        :param fields: The Bigpicture fields for one image.
        """

        def _extract_code(value: BigpictureCodeAttributeValue | None) -> str | None:
            return value.code if value is not None else None

        def _extract_codes(
            values: frozenset[BigpictureCodeAttributeValue],
        ) -> list[str] | None:
            return [v.code for v in values] or None

        blocks = fields.blocks
        stains = fields.stains

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
                fields.image_id,
                fields.dataset_id,
                fields.dataset_image_cnt,
                fields.dataset_short_name,
                fields.dataset_title,
                fields.dataset_description,
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
                                    "anatomical_site": _extract_codes(
                                        block.anatomical_site
                                    ),
                                    "fixation_type": _extract_code(block.fixation_type),
                                    "fixation_type_text": block.fixation_type_text,
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
                                    "staining_substance": _extract_code(
                                        stain.staining_substance
                                    ),
                                    "staining_substance_text": stain.staining_substance_text,
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

    @staticmethod
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

        def _convert_codes(key: str, _dict: dict) -> dict:
            v = _dict.get(key)
            if not v:
                return {}
            codes = v if isinstance(v, list) else [v]
            return {
                key: frozenset(
                    BigpictureCodeAttributeValue(code=c, meaning=c) for c in codes
                )
            }

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
                    **_convert_codes("anatomical_site", block),
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
                    **_convert_code("staining_substance", stain),
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

    async def sync_fields(self, cur: AsyncCursor, image_id: str | None = None) -> None:
        """
        Sync database fields to OpenSearch.

        :param cur: The database cursor.
        :param image_id: An optional image id.
        """

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
                await index_documents(
                    self._search, BP_OPENSEARCH_INDEX, ids_batch, docs_batch
                )

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
                    "blocks": _convert_blocks_for_opensearch(blocks),
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

    @staticmethod
    async def sync_count(cur: AsyncCursor) -> int:
        """
        Return number of images to sync to OpenSearch.

        :param cur: The database cursor.
        :return: The number of images to sync to OpenSearch.
        """

        # Find imaged to sync to OpenSearch.

        await cur.execute("""
            SELECT COUNT(1)
            FROM bp_image
            WHERE search_sync = false
        """)

        return (await cur.fetchone())[0]  # type: ignore
