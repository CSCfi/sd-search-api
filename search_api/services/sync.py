"""Generic sync service: push stored documents to OpenSearch."""

import asyncio
import logging

from opensearchpy import AsyncOpenSearch
from psycopg import AsyncCursor

from search_api.api.opensearch.client import create_search
from search_api.api.opensearch.index import (
    delete_all_documents as delete_all_opensearch_documents,
)
from search_api.api.opensearch.index import (
    delete_documents_by_field as delete_opensearch_documents_by_field,
)
from search_api.api.opensearch.index import (
    delete_documents_by_ids as delete_opensearch_documents_by_ids,
)
from search_api.api.opensearch.index import (
    index_documents,
)
from search_api.database.document import (
    delete_all_documents as delete_all_database_documents,
)
from search_api.database.document import (
    delete_document as delete_database_document,
)
from search_api.database.document import (
    delete_documents_by_set as delete_database_documents_by_set,
)
from search_api.database.document import (
    get_document_ids_by_sets,
    iter_unsynced,
    mark_synced,
    unsynced_count,
)
from search_api.database.document_log import (
    delete_all_document_logs,
    delete_document_logs,
)
from search_api.database.models import UnsyncedDocument
from search_api.database.repository import get_cursor
from search_api.exceptions import SystemException

logger = logging.getLogger(__name__)

# OpenSearch index batch size.
BATCH_SIZE = 1000


class SyncService:
    """Service for syncing stored documents to an OpenSearch index."""

    def __init__(
        self, opensearch_index: str, document_set_id_field: str | None = None
    ) -> None:
        self._opensearch_index = opensearch_index
        self._document_set_id_field = document_set_id_field
        self._search: AsyncOpenSearch = create_search()
        self._task: asyncio.Task | None = None

    @property
    def search(self) -> AsyncOpenSearch:
        return self._search

    def start(self, interval_seconds: float = 60.0) -> None:
        """Start a periodic background task that syncs the database to OpenSearch."""
        if self._task is not None and not self._task.done():
            logger.warning("Sync background task is already running.")
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
                logger.exception("Error during periodic sync.")
            await asyncio.sleep(interval_seconds)

    async def sync_fields(self, cur: AsyncCursor, doc_id: str | None = None) -> None:
        """
        Sync documents to OpenSearch.

        New documents are added and updated ones are replaced. Documents in a document
        set that have been removed from the database, are deleted also from the
        OpenSearch index.

        :param cur: The database cursor.
        :param doc_id: An optional document id to restrict the sync to.
        """

        logger.info("Finding documents to sync to OpenSearch.")

        batch: list[UnsyncedDocument] = []
        # The document sets whose documents were deleted from the OpenSearch index
        # after being deleted from the database.
        pruned_set_ids: set[str] = set()

        async with get_cursor() as update_cur:

            async def flush_batch():
                # Documents are pruned from the OpenSearch index before the batch is
                # marked synced. If pruning fails, the next sync will attempt pruning
                # again.
                set_ids = self._document_set_ids(batch) - pruned_set_ids
                await self._prune_opensearch_document_sets(update_cur, set_ids)
                pruned_set_ids.update(set_ids)

                # Flush the OpenSearch batch.
                logger.info(f"Syncing {len(batch)} documents to OpenSearch.")
                await index_documents(
                    self._search,
                    self._opensearch_index,
                    [doc.id for doc in batch],
                    [doc.payload for doc in batch],
                )

                # Update sync state in the document store.
                logger.info("Updating sync status.")
                marked = await mark_synced(update_cur, batch)
                if marked < len(batch):
                    logger.info(
                        "%d document(s) changed during the sync, and stay pending.",
                        len(batch) - marked,
                    )

                # Clear the OpenSearch batch.
                batch.clear()

            async for doc in iter_unsynced(cur, doc_id):
                batch.append(doc)

                if len(batch) >= BATCH_SIZE:
                    # Flush batch.
                    await flush_batch()

            if batch:
                # Flush final batch.
                await flush_batch()

    def _document_set_ids(self, documents: list[UnsyncedDocument]) -> set[str]:
        """Return the document set ids the documents belong to.

        :param documents: The documents about to be synced.
        :raises SystemException: If a document has no document set id.
        """
        field_id = self._document_set_id_field
        if field_id is None:
            return set()
        set_ids = set()
        for doc in documents:
            set_id = doc.payload.get(field_id)
            if not isinstance(set_id, str):
                raise SystemException(
                    f"Document {doc.id} has no document set id in field '{field_id}'."
                )
            set_ids.add(set_id)
        return set_ids

    async def _prune_opensearch_document_sets(
        self, cur: AsyncCursor, set_ids: set[str]
    ) -> None:
        """Prune OpenSearch documents from the index that are no longer part of the current document set.

        Documents in a document set that have been removed from the database, are deleted also from the
        OpenSearch index.

        :param cur: The database cursor.
        :param set_ids: The ids of the document sets to prune.
        """
        field_id = self._document_set_id_field
        if field_id is None:
            return

        # One query for all the sets costs one index refresh.
        count = await delete_opensearch_documents_by_field(
            self._search,
            self._opensearch_index,
            field_id,
            list(set_ids),
            await get_document_ids_by_sets(cur, list(set_ids)),
        )
        if count:
            logger.info("Pruned %d document(s) from the index.", count)

    @staticmethod
    async def unsynced_count(cur: AsyncCursor) -> int:
        """Return the number of documents pending sync to OpenSearch."""
        return await unsynced_count(cur)

    async def delete_document(self, cur: AsyncCursor, doc_id: str) -> None:
        """Delete a document and its log rows from both stores.

        The document is deleted from the index even if it no longer exists in the
        database.
        """
        deleted = await delete_database_document(cur, doc_id)
        logger.info("Deleted %d document(s) from the database.", int(deleted))
        log_count = await delete_document_logs(cur, [doc_id])
        logger.info("Deleted %d document log row(s).", log_count)

        opensearch_count = await delete_opensearch_documents_by_ids(
            self._search, self._opensearch_index, [doc_id]
        )
        logger.info(
            "Deleted %d document(s) from the '%s' OpenSearch index.",
            opensearch_count,
            self._opensearch_index,
        )

    async def delete_document_set(
        self, cur: AsyncCursor, field_id: str, value: str
    ) -> list[str]:
        """Delete a document set's documents and their log rows from both stores.

        The documents are deleted from the index even if they no longer exist in the
        database.

        The log rows are found only by the ids of the documents deleted from the
        database. So the database deletes run in one transaction, which the index
        delete must succeed in too. If anything fails, the database keeps the documents
        and their log rows, and a rerun finds them again.

        :param cur: The database cursor.
        :param field_id: The top-level OpenSearch field containing the document set id.
        :param value: The document set id.
        :return: The ids of the documents deleted from the database.
        """
        async with cur.connection.transaction():
            ids = await delete_database_documents_by_set(cur, value)
            logger.info("Deleted %d document(s) from the database.", len(ids))
            log_count = await delete_document_logs(cur, ids)
            logger.info("Deleted %d document log row(s).", log_count)

            opensearch_count = await delete_opensearch_documents_by_field(
                self._search,
                self._opensearch_index,
                field_id,
                [value],
                [],
            )
            logger.info(
                "Deleted %d document(s) from the '%s' OpenSearch index.",
                opensearch_count,
                self._opensearch_index,
            )
        return ids

    async def delete_all_documents(self, cur: AsyncCursor) -> None:
        """Delete all documents and their log rows from the database and the OpenSearch index."""
        database_count = await delete_all_database_documents(cur)
        logger.info("Deleted %d document(s) from the database.", database_count)
        log_count = await delete_all_document_logs(cur)
        logger.info("Deleted %d document log row(s).", log_count)

        opensearch_count = await delete_all_opensearch_documents(
            self._search, self._opensearch_index
        )
        logger.info(
            "Deleted %d document(s) from the '%s' OpenSearch index.",
            opensearch_count,
            self._opensearch_index,
        )
