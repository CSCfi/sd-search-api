# OpenSearch service.
import logging

from opensearchpy import OpenSearch, helpers
from typing import Any

logging.basicConfig(level=logging.INFO)


def _search(host: str, port: int, user: str, password: str) -> OpenSearch:
    return OpenSearch(
        hosts=[{"host": host, "port": port}],
        http_auth=(user, password),
        use_ssl=False,
        verify_certs=False,
    )


# TODO(improve): read connection details from an environmental variable
bp_search = _search("localhost", 9200, "admin", "admin")


def index_document(
        search: OpenSearch,
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

    search.index(
        index=index,
        id=id,
        body=doc,
        refresh=False,
    )


def bp_index_document(doc: dict[str, Any]) -> None:
    """
    Index BigPicture document in OpenSearch.

    :param doc: the OpenSearch document to index.
    """
    index_document(bp_search, "bp-image-index", doc["image_id"], doc)


def index_documents(
        search: OpenSearch,
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

    success, failed = helpers.bulk(
        search,
        actions,
        refresh=False,
        chunk_size=1000,
        raise_on_error=False
    )

    if failed:
        logging.error(f"{failed} documents failed to index")


def bp_index_documents(
        ids: list[str],
        docs: list[dict[str, Any]],
) -> None:
    """
    Bulk index BigPicture documents in OpenSearch.

    :param ids: Document ids.
    :param docs: The OpenSearch documents to index.
    """
    index_documents(bp_search, "bp-image-index", ids, docs)
