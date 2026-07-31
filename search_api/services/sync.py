"""Generic sync service: push stored documents to OpenSearch."""

import asyncio
import logging
from typing import Any

from opensearchpy import AsyncOpenSearch
from psycopg import AsyncCursor

from search_api.api.opensearch.services import (
    create_search,
    delete_all_documents as delete_all_opensearch_documents,
    index_documents,
)
from search_api.database.document import (
    delete_all_documents as delete_all_database_documents,
    iter_unsynced,
    mark_synced,
    unsynced_count,
)
from search_api.database.repository import get_cursor

logging.basicConfig(level=logging.INFO)

# OpenSearch index batch size.
BATCH_SIZE = 1000


class SyncService:
    """Service for syncing stored documents to an OpenSearch index."""

    def __init__(self, opensearch_index: str) -> None:
        self._opensearch_index = opensearch_index
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

    async def sync_fields(self, cur: AsyncCursor, doc_id: str | None = None) -> None:
        """
        Sync unsynced documents to OpenSearch.

        :param cur: The database cursor.
        :param doc_id: An optional document id to restrict the sync to.
        """

        logging.info("Finding documents to sync to OpenSearch.")

        ids_batch: list[str] = []
        docs_batch: list[dict[str, Any]] = []

        async with get_cursor() as update_cur:

            async def flush_batch():
                # Flush the OpenSearch batch.
                logging.info(f"Syncing {len(docs_batch)} documents to OpenSearch.")
                await index_documents(
                    self._search, self._opensearch_index, ids_batch, docs_batch
                )

                # Update sync state in the document store.
                logging.info("Updating sync status.")
                await mark_synced(update_cur, ids_batch)

                # Clear the OpenSearch batch.
                ids_batch.clear()
                docs_batch.clear()

            async for row_id, payload in iter_unsynced(cur, doc_id):
                ids_batch.append(row_id)
                docs_batch.append(dict(payload))

                if len(docs_batch) >= BATCH_SIZE:
                    # Flush batch.
                    await flush_batch()

            if ids_batch:
                # Flush final batch.
                await flush_batch()

    @staticmethod
    async def unsynced_count(cur: AsyncCursor) -> int:
        """Return the number of documents pending sync to OpenSearch."""
        return await unsynced_count(cur)

    async def delete_all_documents(self, cur: AsyncCursor) -> None:
        """Delete all documents from the database and the OpenSearch index."""
        database_count = await delete_all_database_documents(cur)
        logging.info("Deleted %d document(s) from the database.", database_count)

        opensearch_count = await delete_all_opensearch_documents(
            self._search, self._opensearch_index
        )
        logging.info(
            "Deleted %d document(s) from the '%s' OpenSearch index.",
            opensearch_count,
            self._opensearch_index,
        )
