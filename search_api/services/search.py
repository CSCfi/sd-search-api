# OpenSearch service.
import asyncio
import atexit
import logging
from typing import Any

from opensearchpy import AsyncOpenSearch, helpers

from search_api.conf import common_config as _common_config

logging.basicConfig(level=logging.INFO)


def _search(host: str, port: int, user: str, password: str) -> AsyncOpenSearch:
    return AsyncOpenSearch(
        hosts=[{"host": host, "port": port}],
        http_auth=(user, password),
        use_ssl=False,
        verify_certs=False,
    )


_cfg = _common_config()
bp_search = _search(
    _cfg.OPENSEARCH_HOST,
    _cfg.OPENSEARCH_PORT,
    _cfg.OPENSEARCH_USER,
    _cfg.OPENSEARCH_PASSWORD,
)


def _close_bp_search():
    try:
        asyncio.run(bp_search.close())
    except Exception:
        pass


atexit.register(_close_bp_search)


async def index_document(
    search: AsyncOpenSearch,
    index: str,
    id: str,
    doc: dict[str, Any],
) -> None:
    """
    Index a document in OpenSearch.

    :param search: The OpenSearch client.
    :param index: The OpenSearch index name.
    :param id: Document id.
    :param doc: The OpenSearch document to index.
    """

    await search.index(
        index=index,
        id=id,
        body=doc,
        refresh=False,
    )


async def bp_index_document(doc: dict[str, Any]) -> None:
    """
    Index BigPicture document in OpenSearch.

    :param doc: the OpenSearch document to index.
    """
    await index_document(bp_search, "bp-image-index", doc["image_id"], doc)


async def index_documents(
    search: AsyncOpenSearch,
    index: str,
    ids: list[str],
    docs: list[dict[str, Any]],
) -> None:
    """
    Bulk index documents in OpenSearch.

    :param search: The OpenSearch client.
    :param index: The OpenSearch index name.
    :param ids: Document ids.
    :param docs: The OpenSearch documents to index.
    """

    if len(ids) != len(docs):
        raise ValueError("Different number of ids and docs")

    actions = (
        {
            "_index": index,
            "_id": _id,
            "_source": doc,
        }
        for _id, doc in zip(ids, docs)
    )

    success, failed = await helpers.async_bulk(
        search, actions, refresh=False, chunk_size=1000, raise_on_error=False
    )

    if failed:
        logging.error(f"{failed} documents failed to index")


async def bp_index_documents(
    ids: list[str],
    docs: list[dict[str, Any]],
) -> None:
    """
    Bulk index BigPicture documents in OpenSearch.

    :param ids: Document ids.
    :param docs: The OpenSearch documents to index.
    """
    await index_documents(bp_search, "bp-image-index", ids, docs)
