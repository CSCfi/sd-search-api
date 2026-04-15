# OpenSearch service.

from opensearchpy import OpenSearch
from typing import Any


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
    index: str,
    id: str,
    doc: dict[str, Any],
) -> None:
    """
    Index a document in OpenSearch.

    :param index: OpenSearch index name.
    :param id: Document id.
    :param doc: The OpenSearch document to index.
    """

    bp_search.index(
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
    index_document("bp-image-index", doc["image_id"], doc)
