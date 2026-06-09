import json
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from dotenv import load_dotenv
from opensearchpy import helpers

load_dotenv(Path(__file__).parent / ".env")

from search_api.api.opensearch.services import create_search  # noqa: E402

bp_search = create_search()

_BP_INDEX_PATH = (
    Path(__file__).resolve().parents[2]
    / "search_api"
    / "opensearch"
    / "bigpicture"
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
