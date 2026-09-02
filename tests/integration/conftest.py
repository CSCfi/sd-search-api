import json
import os
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
import pytest
import pytest_asyncio
from dotenv import load_dotenv
from opensearchpy import helpers

load_dotenv(Path(__file__).parent / ".env")

from search_api.api.opensearch.client import create_search  # noqa: E402
from tests.integration.mockauth import MockAuthProvider  # noqa: E402

bp_search = create_search()

_SUBMIT_MARKER = "requires_submit"
_SNOWSTORM_MARKER = "requires_snowstorm"
_SKIP_SNOWSTORM_ENV = "SKIP_SNOWSTORM_TESTS"
_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})


def _submit_available() -> bool:
    """Whether the SD submit API is available."""

    url = os.environ.get("BP_SUBMIT_API_URL", "").strip()
    if not url:
        return False
    try:
        response = httpx.get(f"{url.rstrip('/')}/sync/submissions", timeout=2.0)
    except httpx.HTTPError:
        return False
    return response.status_code == 401


def pytest_configure(config: pytest.Config) -> None:
    """Register the Snowstorm marker.

    Registered rather than used bare, so a mistyped mark fails the run under
    ``--strict-markers`` instead of quietly never being skipped.
    """
    config.addinivalue_line(
        "markers",
        f"{_SNOWSTORM_MARKER}: needs a reachable Snowstorm, so it is skipped "
        f"when {_SKIP_SNOWSTORM_ENV} is set.",
    )
    config.addinivalue_line(
        "markers",
        f"{_SUBMIT_MARKER}: needs a reachable SD submit API, so it is "
        "skipped when BP_SUBMIT_API_URL answers nothing.",
    )


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    """Skip the tests whose external service is not available.

    GitHub Actions runners can't reach the internal-only Snowstorm route that
    tests/integration/.env points SNOWSTORM_URL at, so CI sets this env var to
    skip those tests there while still running them locally.
    """
    skips = {}
    if os.environ.get(_SKIP_SNOWSTORM_ENV, "").strip().lower() in _TRUE_VALUES:
        skips[_SNOWSTORM_MARKER] = pytest.mark.skip(
            reason=f"Requires a live Snowstorm, and {_SKIP_SNOWSTORM_ENV} is set"
        )
    if not _submit_available():
        skips[_SUBMIT_MARKER] = pytest.mark.skip(
            reason="Requires a reachable Bigpicture submit API"
        )

    for item in items:
        for marker, skip in skips.items():
            if item.get_closest_marker(marker) is not None:
                item.add_marker(skip)


@pytest.fixture(scope="session", autouse=True)
def _mock_oidc_provider():
    """Start the mock OIDC identity provider used by the `client` login fixtures.

    Session-scoped and autouse because the actual sd-search-api server under test
    runs out-of-process (started separately, see CLAUDE.md); nothing in-process can
    be monkeypatched, so idpyoidc RPHandler needs a real, reachable IdP to talk
    to for the duration of the whole test session.

    Skip starting if already running (e.g. from docker-compose's mockauth container).
    """
    import socket

    # Check if mock OIDC is already listening on localhost:8998
    try:
        s = socket.create_connection(("127.0.0.1", 8998), timeout=1)
        s.close()
        yield
        return
    except OSError:
        pass

    # Start the mock provider if not already running
    provider = MockAuthProvider()
    provider.start()
    yield
    provider.stop()


@pytest_asyncio.fixture(scope="session", autouse=True)
async def _close_search_client():
    yield
    await bp_search.close()


_BP_INDEX_PATH = (
    Path(__file__).resolve().parents[2]
    / "search_api"
    / "api"
    / "bigpicture"
    / "index"
    / "bp-image-index.json"
)


@asynccontextmanager
async def create_opensearch_index(
    index_name: str,
    index: Path,
    docs: list[dict[str, Any]],
    id_field: str,
):
    """Create and load an OpenSearch index (delete, create, load docs, yield, delete).

    Args:
        index_name: Name of the index to create.
        index: Path to the JSON file containing the index body.
        docs: Documents to bulk-load.
        id_field: Document field to use as the OpenSearch _id.
    """
    if await bp_search.indices.exists(index=index_name):
        await bp_search.indices.delete(index=index_name)
    await bp_search.indices.create(index=index_name, body=json.loads(index.read_text()))
    actions = [
        {"_index": index_name, "_id": doc[id_field], "_source": doc} for doc in docs
    ]
    await helpers.async_bulk(bp_search, actions)
    await bp_search.indices.refresh(index=index_name)
    yield
    await bp_search.indices.delete(index=index_name)


@pytest.fixture(scope="module")
def bp_opensearch_docs() -> list[dict[str, Any]]:
    """Documents to load into the Bigpicture test index.

    Override this fixture in a test module.
    """
    return []


@pytest.fixture(scope="module")
def bp_opensearch_index_name() -> str:
    """Unique name of the Bigpicture test index."""

    return f"bp-image-index-test-{uuid.uuid4().hex}"


@pytest_asyncio.fixture(scope="module")
async def bp_opensearch_index(
    bp_opensearch_docs: list[dict[str, Any]],
    bp_opensearch_index_name: str,
):
    """Create a Bigpicture test index (create, load docs, yield, delete)."""
    async with create_opensearch_index(
        bp_opensearch_index_name,
        index=_BP_INDEX_PATH,
        docs=bp_opensearch_docs,
        id_field="image_id",
    ):
        yield
