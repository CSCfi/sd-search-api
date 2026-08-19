"""OpenSearch client construction."""

import logging

from opensearchpy import AsyncOpenSearch

from search_api.conf import opensearch_config as _opensearch_config

logging.basicConfig(level=logging.INFO)


def create_search() -> AsyncOpenSearch:
    """Create an OpenSearch client from the application configuration.

    :return: A configured OpenSearch async client.
    """
    cfg = _opensearch_config()
    return AsyncOpenSearch(
        hosts=[{"host": cfg.OPENSEARCH_HOST, "port": cfg.OPENSEARCH_PORT}],
        http_auth=(cfg.OPENSEARCH_USER, cfg.OPENSEARCH_PASSWORD),
        use_ssl=True,
        verify_certs=False,
    )
