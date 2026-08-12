import argparse
import io
import os
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from crypt4gh.keys import get_public_key as c4gh_get_public_key
from crypt4gh.keys.c4gh import generate as c4gh_generate
from crypt4gh.lib import encrypt as c4gh_encrypt
from nacl.public import PrivateKey

from scripts.admin import (
    _clear,
    _load,
    _recreate,
    _recreate_index,
    _sync,
)
from search_api.api.bigpicture.domain import BP_DOMAIN
from search_api.services.load import LoadService
from search_api.database.document import DOCUMENT_TABLE, get_document
from search_api.api.opensearch.services import create_search
from search_api.database.models import StoredTerm
from search_api.database.repository import get_connection
from search_api.database.terms_cache import (
    TERMS_CACHE_TABLE,
    insert_terms,
    read_terms,
)
from search_api.exceptions import SystemException

os.environ.setdefault("POSTGRES_DB", os.environ["BP_POSTGRES_DB"])
os.environ.setdefault("POSTGRES_PORT", os.environ["BP_POSTGRES_PORT"])

_XML_DIR = (
    Path(__file__).resolve().parent.parent.parent.parent
    / "files"
    / "bigpicture"
    / "xml"
)
_XML_METADATA_FILES = [
    "METADATA/dataset.xml",
    "METADATA/image.xml",
    "METADATA/policy.xml",
    "METADATA/sample.xml",
    "METADATA/staining.xml",
]
_CLINICAL_DATASET_DIR = "dataset_clinical"
_CLINICAL_DATASET_ID = "bb-dataset-hy4m2v-9tq7cx"
_CLINICAL_IMAGE_IDS = ["bb-image-k3n8pw-6dz2rj", "bb-image-q7v5tb-m4hs8n"]
_NON_CLINICAL_DATASET_ID = "bb-dataset-w2j6fd-3npx7k"
_NON_CLINICAL_IMAGE_IDS = ["bb-image-z9c4gs-7bqm2t", "bb-image-v6h3rn-8kwd5p"]

# Loading the whole xml/ directory yields both datasets: image id -> (dataset id, scope).
_EXPECTED_DOCUMENTS = {
    **{
        image_id: (_CLINICAL_DATASET_ID, "clinical") for image_id in _CLINICAL_IMAGE_IDS
    },
    **{
        image_id: (_NON_CLINICAL_DATASET_ID, "non_clinical")
        for image_id in _NON_CLINICAL_IMAGE_IDS
    },
}


# Rows the tests below insert to check what a command leaves behind.
_SENTINEL_DOCUMENT_ID = "sentinel"
_SENTINEL_ONTOLOGY_ID = "TEST-clear"


def _args(**kwargs) -> argparse.Namespace:
    defaults = dict(
        directory=str(_XML_DIR),
        multi_dir=True,
        dry_run=False,
        sync=False,
        c4gh_key_file=None,
        c4gh_passphrase=None,
    )
    return argparse.Namespace(**{**defaults, **kwargs})


@pytest_asyncio.fixture(autouse=True)
async def delete_test_rows():
    """Delete everything these tests write, before and after each of them.

    Rows are deleted before and after the test runs. Some rows are
    intentionally left behind by the tests and must be removed.
    """

    async def _delete() -> None:
        async with get_connection() as conn:
            async with conn.cursor() as cur:
                for image_id in (*_EXPECTED_DOCUMENTS, _SENTINEL_DOCUMENT_ID):
                    await cur.execute(
                        f"DELETE FROM {DOCUMENT_TABLE} WHERE id = %s", (image_id,)
                    )
                await cur.execute(
                    f"DELETE FROM {TERMS_CACHE_TABLE} WHERE ontology_id = %s",
                    (_SENTINEL_ONTOLOGY_ID,),
                )

    await _delete()
    yield
    await _delete()


@pytest.mark.asyncio
async def test_load_extract_only():
    with patch.object(
        LoadService, "store_document", new_callable=AsyncMock
    ) as load_spy:
        await _load(BP_DOMAIN, _args(dry_run=True))
        load_spy.assert_not_called()

    async with get_connection() as conn:
        async with conn.cursor() as cur:
            for image_id in _EXPECTED_DOCUMENTS:
                payload = await get_document(cur, image_id)
                assert payload is None, (
                    f"{image_id!r} was unexpectedly written during dry-run"
                )


@pytest.mark.requires_snowstorm
@pytest.mark.asyncio
async def test_load_plain_files():
    """The load command inserts clinical and non-clinical datasets."""
    await _load(BP_DOMAIN, _args())

    async with get_connection() as conn:
        async with conn.cursor() as cur:
            for image_id, (dataset_id, scope) in _EXPECTED_DOCUMENTS.items():
                payload = await get_document(cur, image_id)
                assert payload is not None, f"{image_id!r} was not loaded"
                assert payload["image_id"] == image_id
                assert payload["dataset_id"] == dataset_id
                assert payload["scope"] == scope


@pytest.mark.requires_snowstorm
@pytest.mark.asyncio
async def test_load_c4gh_files(tmp_path):
    """The load command decrypts Crypt4GH-encrypted XML files and inserts them into the database."""
    seckey_path = tmp_path / "key.sec"
    pubkey_path = tmp_path / "key.pub"
    c4gh_generate(str(seckey_path), str(pubkey_path), b"", b"")

    recipient_pk = c4gh_get_public_key(str(pubkey_path))
    sender_sk = bytes(PrivateKey.generate())

    # Only the clinical dataset directory is tested.
    metadata_dir = tmp_path / _CLINICAL_DATASET_DIR / "METADATA"
    metadata_dir.mkdir(parents=True)
    for xml_file in _XML_METADATA_FILES:
        src = _XML_DIR / _CLINICAL_DATASET_DIR / xml_file
        dst = metadata_dir / (Path(xml_file).name + ".c4gh")
        with dst.open("wb") as outfile:
            c4gh_encrypt(
                [(0, sender_sk, recipient_pk)],
                io.BytesIO(src.read_bytes()),
                outfile,
            )

    await _load(
        BP_DOMAIN,
        _args(
            directory=str(tmp_path),
            c4gh_key_file=str(seckey_path),
        ),
    )

    async with get_connection() as conn:
        async with conn.cursor() as cur:
            for image_id in _CLINICAL_IMAGE_IDS:
                payload = await get_document(cur, image_id)
                assert payload is not None, f"{image_id!r} was not loaded"
                assert payload["image_id"] == image_id
                assert payload["dataset_id"] == _CLINICAL_DATASET_ID


# Test recreate.
#


def _recreate_args() -> argparse.Namespace:
    return argparse.Namespace(group="Bigpicture")


@pytest.mark.asyncio
async def test_recreate_refused_in_production(monkeypatch):
    """The command destroys everything, so production must be unreachable."""
    monkeypatch.setenv("DEPLOYMENT_ENV", "prod")

    with pytest.raises(SystemException, match="not available in production"):
        await _recreate(BP_DOMAIN, _recreate_args())


@pytest.mark.asyncio
async def test_recreate_aborts_unless_confirmed(monkeypatch):
    """A wrong answer must leave the schema and its data untouched."""
    monkeypatch.setattr("builtins.input", lambda _prompt: "not the group name")

    await _store_sentinel_document()

    await _recreate(BP_DOMAIN, _recreate_args())

    async with get_connection() as conn:
        async with conn.cursor() as cur:
            assert await get_document(cur, _SENTINEL_DOCUMENT_ID) is not None


@pytest.mark.asyncio
async def test_recreate_rebuilds_the_schema_and_the_index(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _prompt: "Bigpicture")

    search = create_search()
    try:
        # A stale field, as an index created before a mapping change could have.
        if await search.indices.exists(index=BP_DOMAIN.opensearch_index):
            await search.indices.delete(index=BP_DOMAIN.opensearch_index)
        await search.indices.create(
            index=BP_DOMAIN.opensearch_index,
            body={"mappings": {"properties": {"stale_field": {"type": "text"}}}},
        )
        await _store_sentinel_document()

        await _recreate(BP_DOMAIN, _recreate_args())

        # The schema is back and empty.
        async with get_connection() as conn:
            async with conn.cursor() as cur:
                assert await get_document(cur, _SENTINEL_DOCUMENT_ID) is None
                await cur.execute(
                    "SELECT table_name FROM information_schema.tables"
                    " WHERE table_schema = 'public'"
                )
                tables = {row[0] for row in await cur.fetchall()}
        assert {"document", "terms_cache", "ontology_cache"} <= tables

        # The index carries the generated mapping, not the stale one.
        mapping = await search.indices.get_mapping(index=BP_DOMAIN.opensearch_index)
        properties = mapping[BP_DOMAIN.opensearch_index]["mappings"]["properties"]
        assert "stale_field" not in properties
        assert properties["scope"]["type"] == "keyword"
        assert properties["diagnosis"]["type"] == "nested"
        assert properties["diagnosis"]["properties"]["qualifiers"]["type"] == "keyword"
    finally:
        await search.close()


# index recreate and sync
#


def _index_args() -> argparse.Namespace:
    return argparse.Namespace(group="Bigpicture")


async def _sentinel_is_synced() -> bool:
    """Return True if the sentinel document is stamped as synced."""
    async with get_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                f"SELECT synced_at FROM {DOCUMENT_TABLE} WHERE id = %s",
                (_SENTINEL_DOCUMENT_ID,),
            )
            row = await cur.fetchone()
    return row is not None and row[0] is not None


@pytest.mark.asyncio
async def test_index_recreate_refused_in_production(monkeypatch):
    monkeypatch.setenv("DEPLOYMENT_ENV", "prod")

    with pytest.raises(SystemException, match="not available in production"):
        await _recreate_index(BP_DOMAIN, _index_args())


@pytest.mark.asyncio
async def test_index_recreate_aborts_unless_confirmed(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _prompt: "not the group name")
    search = create_search()
    try:
        await _store_sentinel_document()
        await _sync(BP_DOMAIN)

        await _recreate_index(BP_DOMAIN, _index_args())

        assert await search.exists(
            index=BP_DOMAIN.opensearch_index, id=_SENTINEL_DOCUMENT_ID
        )
        assert await _sentinel_is_synced(), "the sync state was reset by an abort"
    finally:
        await search.close()


@pytest.mark.asyncio
async def test_index_recreate_rebuilds_the_mapping_and_sync_refills_it(monkeypatch):
    """The documents stay in the database, so a plain sync refills the index."""
    monkeypatch.setattr("builtins.input", lambda _prompt: "Bigpicture")
    search = create_search()
    try:
        # A stale mapping, as an index created before a mapping change would have.
        if await search.indices.exists(index=BP_DOMAIN.opensearch_index):
            await search.indices.delete(index=BP_DOMAIN.opensearch_index)
        await search.indices.create(
            index=BP_DOMAIN.opensearch_index,
            body={"mappings": {"properties": {"stale_field": {"type": "text"}}}},
        )
        await _store_sentinel_document()
        await _sync(BP_DOMAIN)
        assert await _sentinel_is_synced()

        await _recreate_index(BP_DOMAIN, _index_args())

        # The index is there, carrying the generated mapping rather than the stale one.
        mapping = await search.indices.get_mapping(index=BP_DOMAIN.opensearch_index)
        properties = mapping[BP_DOMAIN.opensearch_index]["mappings"]["properties"]
        assert "stale_field" not in properties
        assert properties["diagnosis"]["type"] == "nested"

        # Empty, and every document is pending again, so a sync refills it.
        await search.indices.refresh(index=BP_DOMAIN.opensearch_index)
        assert not await search.exists(
            index=BP_DOMAIN.opensearch_index, id=_SENTINEL_DOCUMENT_ID
        )
        assert not await _sentinel_is_synced()

        await _sync(BP_DOMAIN)
        await search.indices.refresh(index=BP_DOMAIN.opensearch_index)
        assert await search.exists(
            index=BP_DOMAIN.opensearch_index, id=_SENTINEL_DOCUMENT_ID
        )
    finally:
        await search.close()


# clear
#


def _clear_args() -> argparse.Namespace:
    return argparse.Namespace(group="Bigpicture")


async def _store_sentinel_document() -> None:
    """Store a sentinel document to check later if it exists."""
    async with get_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                f"INSERT INTO {DOCUMENT_TABLE} (id, payload) VALUES (%s, '{{}}')",
                (_SENTINEL_DOCUMENT_ID,),
            )


async def _sentinel_document_exists() -> bool:
    """Return True if the sentinel document exists."""
    async with get_connection() as conn:
        async with conn.cursor() as cur:
            return await get_document(cur, _SENTINEL_DOCUMENT_ID) is not None


async def _store_sentinel_term() -> None:
    """Cache a preferred term, to check later if it exists."""
    await insert_terms(
        _SENTINEL_ONTOLOGY_ID,
        [StoredTerm(field_id="f1", concept_id="c1", preferred_term="P1")],
    )


async def _sentinel_term_exists() -> bool:
    """Return True if the cached preferred term exists."""
    return bool(await read_terms(_SENTINEL_ONTOLOGY_ID))


@pytest.mark.asyncio
async def test_clear_refused_in_production(monkeypatch):
    monkeypatch.setenv("DEPLOYMENT_ENV", "prod")
    await _store_sentinel_document()
    await _store_sentinel_term()

    with pytest.raises(SystemException, match="not available in production"):
        await _clear(BP_DOMAIN, _clear_args())

    assert await _sentinel_document_exists()
    assert await _sentinel_term_exists()


@pytest.mark.asyncio
async def test_clear_aborts_unless_confirmed(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _prompt: "not the group name")
    await _store_sentinel_document()
    await _store_sentinel_term()

    await _clear(BP_DOMAIN, _clear_args())

    assert await _sentinel_document_exists()
    assert await _sentinel_term_exists()


@pytest.mark.asyncio
async def test_clear_deletes_every_document(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _prompt: "Bigpicture")
    await _store_sentinel_document()

    await _clear(BP_DOMAIN, _clear_args())

    assert not await _sentinel_document_exists()


@pytest.mark.asyncio
async def test_clear_deletes_the_cached_preferred_terms(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _prompt: "Bigpicture")
    await _store_sentinel_term()

    await _clear(BP_DOMAIN, _clear_args())

    assert not await _sentinel_term_exists()
