"""Bigpicture sync service."""

import asyncio
import logging
from typing import Any

import isodate  # type: ignore[import-untyped]
from opensearchpy import AsyncOpenSearch
from psycopg import AsyncCursor

from search_api.api.bigpicture.models import BP_OPENSEARCH_INDEX
from search_api.api.opensearch.services import (
    create_search,
    index_documents,
    iso8601_duration_to_days,
)
from search_api.database.repository import get_cursor

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


class BigPictureSyncService:
    """Service for syncing Bigpicture fields from the database to OpenSearch."""

    def __init__(self) -> None:
        self._search: AsyncOpenSearch = create_search()
        self._task: asyncio.Task | None = None

    @property
    def search(self) -> AsyncOpenSearch:
        return self._search

    def start(self, interval_seconds: float = 60.0) -> None:
        """Start a periodic background task that syncs the database to OpenSearch."""
        if self._task is not None and not self._task.done():
            logging.warning("Sync background task is already running.")
            return
        self._task = asyncio.create_task(self._sync_loop(interval_seconds))

    def stop(self) -> None:
        """Stop the periodic background sync task."""
        if self._task is not None:
            self._task.cancel()
            self._task = None

    async def _sync_loop(self, interval_seconds: float) -> None:
        """Run sync_fields repeatedly, sleeping interval_seconds between runs."""
        while True:
            try:
                async with get_cursor() as cur:
                    await self.sync_fields(cur)
            except asyncio.CancelledError:
                raise
            except Exception:
                logging.exception("Error during periodic sync.")
            await asyncio.sleep(interval_seconds)

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
                WHERE opensearch_synced_at IS NULL
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
                        opensearch_synced_at = now()
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

        await cur.execute("""
            SELECT COUNT(1)
            FROM bp_image
            WHERE opensearch_synced_at IS NULL
        """)

        return (await cur.fetchone())[0]  # type: ignore
