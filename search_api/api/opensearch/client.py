"""OpenSearch client construction."""

from opensearchpy import AsyncOpenSearch

from search_api.conf import opensearch_admin_config as _opensearch_admin_config
from search_api.conf import opensearch_config as _opensearch_config


def create_search() -> AsyncOpenSearch:
    """Create an OpenSearch client for the API and CLI user.

    :return: A configured OpenSearch async client.
    """
    cfg = _opensearch_config()
    return _create(cfg.OPENSEARCH_USER, cfg.OPENSEARCH_PASSWORD.get_secret_value())


def create_admin_search() -> AsyncOpenSearch:
    """Create an OpenSearch client for the admin user.

    :return: A configured OpenSearch async client.
    """
    admin = _opensearch_admin_config()
    return _create(
        admin.OPENSEARCH_ADMIN_USER, admin.OPENSEARCH_ADMIN_PASSWORD.get_secret_value()
    )


def _create(user: str, password: str) -> AsyncOpenSearch:
    """Create an OpenSearch client for a user."""
    cfg = _opensearch_config()
    return AsyncOpenSearch(
        hosts=[{"host": cfg.OPENSEARCH_HOST, "port": cfg.OPENSEARCH_PORT}],
        http_auth=(user, password),
        use_ssl=True,
        verify_certs=cfg.OPENSEARCH_VERIFY_CERTS,
        ca_certs=cfg.OPENSEARCH_CA_CERTS,
    )
