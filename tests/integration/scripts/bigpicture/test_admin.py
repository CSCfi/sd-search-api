import argparse
import io
import json
import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime
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
    _create_index,
    _delete,
    _delete_set,
    _load,
    _read_documents,
    _recreate,
    _recreate_index,
    _sync,
)
from search_api.api.bigpicture.domain import BP_DOMAIN
from search_api.api.bigpicture.models import BP_DATASET_ID_FIELD, BP_DOCUMENT_FIELDS
from search_api.api.opensearch.client import create_search
from search_api.api.opensearch.models import ExtractedDocument, OpenSearchFieldValue
from search_api.database.document import DOCUMENT_TABLE, get_document
from search_api.database.document_log import (
    DOCUMENT_LOG_TABLE,
    read_document_logs,
    write_document_log,
)
from search_api.database.load import delete_load_marker
from search_api.database.models import StoredDocumentLog, StoredTerm
from search_api.database.repository import get_connection
from search_api.database.terms_cache import (
    TERMS_CACHE_TABLE,
    insert_terms,
    read_terms,
)
from search_api.exceptions import SystemException
from search_api.services.fetch import DocumentSet, DocumentSource
from search_api.services.load import LoadService
from tests.integration.bigpicture import (
    assert_dataset_images_stored,
    delete_dataset_images,
)
from tests.utils.bigpicture import (
    CLINICAL_2_0_DATASET_DIR,
    CLINICAL_2_0_DATASET_ID,
    CLINICAL_2_0_IMAGE_IDS,
    DATASET_IMAGES,
    VERSION_DIRS,
    XML_DIR_2_0,
)

os.environ.setdefault("POSTGRES_DB", os.environ["BP_POSTGRES_DB"])
os.environ.setdefault("POSTGRES_PORT", os.environ["BP_POSTGRES_PORT"])

_XML_METADATA_FILES = [
    "METADATA/dataset.xml",
    "METADATA/image.xml",
    "METADATA/policy.xml",
    "METADATA/sample.xml",
    "METADATA/staining.xml",
]


_ONTOLOGY_ID = "test-ontology"

_DOCUMENT_ID = "test-document"
_DATASET_ID = "test-dataset"
_OTHER_DOCUMENT_ID = "test-other-document"
_OTHER_DATASET_ID = "test-other-dataset"

_DATASET_OF_DOCUMENT = {
    _DOCUMENT_ID: _DATASET_ID,
    _OTHER_DOCUMENT_ID: _OTHER_DATASET_ID,
}
_DOCUMENTS_OF_DATASET = {
    _DATASET_ID: [_DOCUMENT_ID],
    _OTHER_DATASET_ID: [_OTHER_DOCUMENT_ID],
}

_VERSIONED_DATASET_ID = "test-versioned-dataset"
_VERSIONED_EXCLUDED_DOCUMENT_ID = "test-excluded-document"
_VERSIONED_INCLUDED_DOCUMENT_ID = "test-included-document"

# All test documents.
_TEST_DOCUMENT_IDS = [
    *_DATASET_OF_DOCUMENT,
    _VERSIONED_EXCLUDED_DOCUMENT_ID,
    _VERSIONED_INCLUDED_DOCUMENT_ID,
]


def _args(**kwargs) -> argparse.Namespace:
    defaults = {
        "directory": str(XML_DIR_2_0),
        "full": False,
        "dry_run": False,
        "sync": False,
    }
    return argparse.Namespace(**{**defaults, **kwargs})


@pytest_asyncio.fixture(autouse=True)
async def delete_test_rows():
    """Delete everything these tests write, before and after each of them.

    Rows are deleted before and after the test runs. Some rows are
    intentionally left behind by the tests and must be removed.
    """

    async def _delete() -> None:
        async with get_connection() as conn, conn.cursor() as cur:
            await delete_dataset_images(cur)
            await cur.execute(
                f"DELETE FROM {DOCUMENT_TABLE} WHERE id = ANY(%s)",
                (_TEST_DOCUMENT_IDS,),
            )
            await cur.execute(
                f"DELETE FROM {TERMS_CACHE_TABLE} WHERE ontology_id = %s",
                (_ONTOLOGY_ID,),
            )
            await cur.execute(
                f"DELETE FROM {DOCUMENT_LOG_TABLE} WHERE document_id = ANY(%s)",
                (_TEST_DOCUMENT_IDS,),
            )

    await _delete()
    yield
    await _delete()


@pytest.mark.asyncio
async def test_load_extract_only():
    with patch.object(
        LoadService, "store_document", new_callable=AsyncMock
    ) as load_spy:
        for version_dir in VERSION_DIRS:
            await _load(BP_DOMAIN, _args(directory=str(version_dir), dry_run=True))
        load_spy.assert_not_called()

    async with get_connection() as conn, conn.cursor() as cur:
        for image_id in DATASET_IMAGES:
            payload = await get_document(cur, image_id)
            assert payload is None, (
                f"{image_id!r} was unexpectedly written during dry-run"
            )


@pytest.mark.requires_snowstorm
@pytest.mark.asyncio
async def test_load_plain_files():
    """The load command inserts clinical and non-clinical datasets of every version."""
    for version_dir in VERSION_DIRS:
        await _load(BP_DOMAIN, _args(directory=str(version_dir)))

    async with get_connection() as conn, conn.cursor() as cur:
        await assert_dataset_images_stored(cur)


@pytest.mark.requires_snowstorm
@pytest.mark.asyncio
async def test_load_c4gh_files(tmp_path, monkeypatch):
    """The load command decrypts Crypt4GH-encrypted XML files and inserts them into the database."""
    seckey_path = tmp_path / "key.sec"
    pubkey_path = tmp_path / "key.pub"
    c4gh_generate(str(seckey_path), str(pubkey_path), b"", b"")

    recipient_pk = c4gh_get_public_key(str(pubkey_path))
    sender_sk = bytes(PrivateKey.generate())

    # Only the clinical dataset directory is tested.
    metadata_dir = tmp_path / CLINICAL_2_0_DATASET_DIR.name / "METADATA"
    metadata_dir.mkdir(parents=True)
    for xml_file in _XML_METADATA_FILES:
        src = CLINICAL_2_0_DATASET_DIR / xml_file
        dst = metadata_dir / (Path(xml_file).name + ".c4gh")
        with dst.open("wb") as outfile:
            c4gh_encrypt(
                [(0, sender_sk, recipient_pk)],
                io.BytesIO(src.read_bytes()),
                outfile,
            )

    monkeypatch.setenv("C4GH_KEY_FILE", str(seckey_path))

    await _load(BP_DOMAIN, _args(directory=str(tmp_path)))

    async with get_connection() as conn, conn.cursor() as cur:
        for image_id in CLINICAL_2_0_IMAGE_IDS:
            payload = await get_document(cur, image_id)
            assert payload is not None, f"{image_id!r} was not loaded"
            assert payload["image_id"] == image_id
            assert payload["dataset_id"] == CLINICAL_2_0_DATASET_ID


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

    await _store_document(_DATASET_ID, _DOCUMENT_ID)

    await _recreate(BP_DOMAIN, _recreate_args())

    async with get_connection() as conn, conn.cursor() as cur:
        assert await get_document(cur, _DOCUMENT_ID) is not None


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
        await _store_document(_DATASET_ID, _DOCUMENT_ID)

        await _recreate(BP_DOMAIN, _recreate_args())

        # The schema is back and empty.
        async with get_connection() as conn, conn.cursor() as cur:
            assert await get_document(cur, _DOCUMENT_ID) is None
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
        assert properties["observation"]["type"] == "nested"
        assert "qualifiers" not in properties["observation"]["properties"]
    finally:
        await search.close()


# index recreate and sync
#


def _index_args() -> argparse.Namespace:
    return argparse.Namespace(group="Bigpicture")


async def _document_is_synced(doc_id: str) -> bool:
    """Return True if the document is stamped as synced."""
    async with get_connection() as conn, conn.cursor() as cur:
        await cur.execute(
            f"SELECT synced_at FROM {DOCUMENT_TABLE} WHERE id = %s",
            (doc_id,),
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
        await _store_document(_DATASET_ID, _DOCUMENT_ID)
        await _sync(BP_DOMAIN)

        await _recreate_index(BP_DOMAIN, _index_args())

        assert await search.exists(index=BP_DOMAIN.opensearch_index, id=_DOCUMENT_ID)
        assert await _document_is_synced(_DOCUMENT_ID), (
            "the sync state was reset by an abort"
        )
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
        await _store_document(_DATASET_ID, _DOCUMENT_ID)
        await _sync(BP_DOMAIN)
        assert await _document_is_synced(_DOCUMENT_ID)

        await _recreate_index(BP_DOMAIN, _index_args())

        # The index is there, carrying the generated mapping rather than the stale one.
        mapping = await search.indices.get_mapping(index=BP_DOMAIN.opensearch_index)
        properties = mapping[BP_DOMAIN.opensearch_index]["mappings"]["properties"]
        assert "stale_field" not in properties
        assert properties["observation"]["type"] == "nested"

        # Empty, and every document is pending again, so a sync refills it.
        await search.indices.refresh(index=BP_DOMAIN.opensearch_index)
        assert not await search.exists(
            index=BP_DOMAIN.opensearch_index, id=_DOCUMENT_ID
        )
        assert not await _document_is_synced(_DOCUMENT_ID)

        await _sync(BP_DOMAIN)
        await search.indices.refresh(index=BP_DOMAIN.opensearch_index)
        assert await search.exists(index=BP_DOMAIN.opensearch_index, id=_DOCUMENT_ID)
    finally:
        await search.close()


# clear
#


def _clear_args() -> argparse.Namespace:
    return argparse.Namespace(group="Bigpicture")


async def _store_document(dataset_id: str, doc_id: str) -> None:
    """Store a document of the dataset, to check later if it exists."""
    async with get_connection() as conn, conn.cursor() as cur:
        await cur.execute(
            f"INSERT INTO {DOCUMENT_TABLE} (id, payload, modified_at, set_id) "
            f"VALUES (%s, %s, now(), %s)",
            (doc_id, json.dumps({BP_DATASET_ID_FIELD: dataset_id}), dataset_id),
        )


async def _stored(doc_id: str) -> bool:
    """Return True if the document is in the database."""
    async with get_connection() as conn, conn.cursor() as cur:
        return await get_document(cur, doc_id) is not None


async def _store_term() -> None:
    """Cache a preferred term, to check later if it exists."""
    await insert_terms(
        _ONTOLOGY_ID,
        [StoredTerm(field_id="f1", concept_id="c1", preferred_term="P1")],
    )


async def _store_log(doc_id: str) -> None:
    """Log a row for the document, to check later if it exists."""
    async with get_connection() as conn, conn.cursor() as cur:
        await write_document_log(
            cur,
            StoredDocumentLog(
                document_id=doc_id,
                severity="ERROR",
                message="Something was not found.",
            ),
        )


async def _log_exists(doc_id: str) -> bool:
    """Return True if the row logged for the document exists."""
    async with get_connection() as conn, conn.cursor() as cur:
        return bool(await read_document_logs(cur, doc_id))


async def _term_exists() -> bool:
    """Return True if the cached preferred term exists."""
    return bool(await read_terms(_ONTOLOGY_ID))


@pytest.mark.asyncio
async def test_clear_refused_in_production(monkeypatch):
    monkeypatch.setenv("DEPLOYMENT_ENV", "prod")
    await _store_document(_DATASET_ID, _DOCUMENT_ID)
    await _store_term()
    await _store_log(_DOCUMENT_ID)

    with pytest.raises(SystemException, match="not available in production"):
        await _clear(BP_DOMAIN, _clear_args())

    assert await _stored(_DOCUMENT_ID)
    assert await _term_exists()
    assert await _log_exists(_DOCUMENT_ID)


@pytest.mark.asyncio
async def test_clear_aborts_unless_confirmed(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _prompt: "not the group name")
    await _store_document(_DATASET_ID, _DOCUMENT_ID)
    await _store_term()
    await _store_log(_DOCUMENT_ID)

    await _clear(BP_DOMAIN, _clear_args())

    assert await _stored(_DOCUMENT_ID)
    assert await _term_exists()
    assert await _log_exists(_DOCUMENT_ID)


@pytest.mark.asyncio
async def test_clear_deletes_every_document(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _prompt: "Bigpicture")
    await _store_document(_DATASET_ID, _DOCUMENT_ID)

    await _clear(BP_DOMAIN, _clear_args())

    assert not await _stored(_DOCUMENT_ID)


@pytest.mark.asyncio
async def test_clear_deletes_the_document_log(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _prompt: "Bigpicture")
    await _store_document(_DATASET_ID, _DOCUMENT_ID)
    await _store_log(_DOCUMENT_ID)

    await _clear(BP_DOMAIN, _clear_args())

    assert not await _log_exists(_DOCUMENT_ID)


@pytest.mark.asyncio
async def test_clear_deletes_the_cached_preferred_terms(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _prompt: "Bigpicture")
    await _store_term()

    await _clear(BP_DOMAIN, _clear_args())

    assert not await _term_exists()


# delete-set
#


def _delete_set_args(document_set: str) -> argparse.Namespace:
    return argparse.Namespace(group="Bigpicture", document_set=document_set)


async def _store_and_index_documents() -> None:
    """Store and sync each test document in its dataset, see _DATASET_OF_DOCUMENT."""
    search = create_search()
    try:
        exists = await search.indices.exists(index=BP_DOMAIN.opensearch_index)
    finally:
        await search.close()
    if not exists:
        await _create_index(BP_DOMAIN)

    for doc_id, dataset_id in _DATASET_OF_DOCUMENT.items():
        await _store_document(dataset_id, doc_id)
    await _sync(BP_DOMAIN)


async def _indexed(doc_id: str) -> bool:
    """Return True if the document is in the OpenSearch index."""
    search = create_search()
    try:
        await search.indices.refresh(index=BP_DOMAIN.opensearch_index)
        return await search.exists(index=BP_DOMAIN.opensearch_index, id=doc_id)
    finally:
        await search.close()


@pytest_asyncio.fixture
async def documents():
    """Store and index _DOCUMENT_ID in _DATASET_ID, and _OTHER_DOCUMENT_ID in _OTHER_DATASET_ID."""
    await _store_and_index_documents()
    yield
    search = create_search()
    try:
        for doc_id in _DATASET_OF_DOCUMENT:
            await search.delete(
                index=BP_DOMAIN.opensearch_index, id=doc_id, ignore=[404]
            )
    finally:
        await search.close()


@pytest.mark.asyncio
async def test_delete_set_aborts_unless_confirmed(monkeypatch, documents):
    monkeypatch.setattr("builtins.input", lambda _prompt: "not the dataset")

    await _delete_set(BP_DOMAIN, _delete_set_args(_DATASET_ID))

    for doc_id in _DOCUMENTS_OF_DATASET[_DATASET_ID]:
        assert await _stored(doc_id)
        assert await _indexed(doc_id)


@pytest.mark.asyncio
async def test_delete_set_deletes_only_the_dataset(monkeypatch, documents):
    monkeypatch.setattr("builtins.input", lambda _prompt: _DATASET_ID)
    await _store_log(_DOCUMENT_ID)

    await _delete_set(BP_DOMAIN, _delete_set_args(_DATASET_ID))

    for doc_id in _DOCUMENTS_OF_DATASET[_DATASET_ID]:
        assert not await _stored(doc_id)
        assert not await _indexed(doc_id)
    assert not await _log_exists(_DOCUMENT_ID)
    for doc_id in _DOCUMENTS_OF_DATASET[_OTHER_DATASET_ID]:
        assert await _stored(doc_id)
        assert await _indexed(doc_id)


@pytest.mark.asyncio
async def test_delete_set_removes_only_indexed(monkeypatch, documents):
    """Documents left only in the index are deleted."""
    monkeypatch.setattr("builtins.input", lambda _prompt: _DATASET_ID)

    # Delete the rows from the database.
    async with get_connection() as conn, conn.cursor() as cur:
        await cur.execute(
            f"DELETE FROM {DOCUMENT_TABLE} WHERE id = ANY(%s)",
            (_DOCUMENTS_OF_DATASET[_DATASET_ID],),
        )

    await _delete_set(BP_DOMAIN, _delete_set_args(_DATASET_ID))

    for doc_id in _DOCUMENTS_OF_DATASET[_DATASET_ID]:
        assert not await _indexed(doc_id)
    for doc_id in _DOCUMENTS_OF_DATASET[_OTHER_DATASET_ID]:
        assert await _indexed(doc_id)


@pytest.mark.asyncio
async def test_delete_set_unknown_dataset_deletes_nothing(monkeypatch, documents):
    monkeypatch.setattr("builtins.input", lambda _prompt: "no-such-dataset")

    await _delete_set(BP_DOMAIN, _delete_set_args("no-such-dataset"))

    for doc_id in _DATASET_OF_DOCUMENT:
        assert await _stored(doc_id)
        assert await _indexed(doc_id)


# delete
#


def _delete_args(document: str) -> argparse.Namespace:
    return argparse.Namespace(group="Bigpicture", document=document)


@pytest.mark.asyncio
async def test_delete_aborts_unless_confirmed(monkeypatch, documents):
    monkeypatch.setattr("builtins.input", lambda _prompt: "not the document")

    await _delete(BP_DOMAIN, _delete_args(_DOCUMENT_ID))

    assert await _stored(_DOCUMENT_ID)
    assert await _indexed(_DOCUMENT_ID)


@pytest.mark.asyncio
async def test_delete_deletes_only_the_document(monkeypatch, documents):
    monkeypatch.setattr("builtins.input", lambda _prompt: _DOCUMENT_ID)
    await _store_log(_DOCUMENT_ID)

    await _delete(BP_DOMAIN, _delete_args(_DOCUMENT_ID))

    assert not await _stored(_DOCUMENT_ID)
    assert not await _indexed(_DOCUMENT_ID)
    assert not await _log_exists(_DOCUMENT_ID)
    assert await _stored(_OTHER_DOCUMENT_ID)
    assert await _indexed(_OTHER_DOCUMENT_ID)


@pytest.mark.asyncio
async def test_delete_removes_only_indexed(monkeypatch, documents):
    """Document left only in the index is deleted."""

    monkeypatch.setattr("builtins.input", lambda _prompt: _DOCUMENT_ID)

    # Delete the row from the database.
    async with get_connection() as conn, conn.cursor() as cur:
        await cur.execute(
            f"DELETE FROM {DOCUMENT_TABLE} WHERE id = %s", (_DOCUMENT_ID,)
        )

    await _delete(BP_DOMAIN, _delete_args(_DOCUMENT_ID))

    assert not await _indexed(_DOCUMENT_ID)
    assert await _indexed(_OTHER_DOCUMENT_ID)


@pytest.mark.asyncio
async def test_delete_unknown_document_deletes_nothing(monkeypatch, documents):
    monkeypatch.setattr("builtins.input", lambda _prompt: "no-such-document")

    await _delete(BP_DOMAIN, _delete_args("no-such-document"))

    assert await _stored(_DOCUMENT_ID)
    assert await _indexed(_DOCUMENT_ID)


# Versions of a dataset
#

_FIRST_VERSION = datetime(2026, 1, 1, tzinfo=UTC)
_NEWER_VERSION = datetime(2026, 2, 1, tzinfo=UTC)
_OLDER_VERSION = datetime(2025, 1, 1, tzinfo=UTC)


def _versioned_document(doc_id: str) -> ExtractedDocument:
    """A document of the versioned dataset with nothing but its dataset id."""
    return ExtractedDocument(
        id=doc_id,
        scope="clinical",
        values=[
            OpenSearchFieldValue(
                field=BP_DOCUMENT_FIELDS[BP_DATASET_ID_FIELD],
                value=_VERSIONED_DATASET_ID,
            )
        ],
    )


class _VersionSource(DocumentSource):
    """Reads one version of the versioned dataset."""

    def __init__(self, doc_ids: list[str], modified_at: datetime) -> None:
        self._modified_at = modified_at
        self._documents = [_versioned_document(i) for i in doc_ids]

    async def read(
        self, root: str | None = None, marker: str | None = None
    ) -> AsyncIterator[DocumentSet]:
        yield DocumentSet(
            id=_VERSIONED_DATASET_ID,
            marker="test",
            modified_at=self._modified_at,
            documents=self._documents,
        )


async def _load_and_sync_version(doc_ids: list[str], modified_at: datetime) -> None:
    """Load and sync the versioned dataset _VERSIONED_DATASET_ID.

    :param doc_ids: The documents this version has.
    :param modified_at: The version's date. It decides if this version is newer
        than the stored one.
    """
    await _read_documents(
        BP_DOMAIN, _VersionSource(doc_ids, modified_at), _args(full=True, sync=True)
    )


@pytest_asyncio.fixture
async def first_version():
    """Load and index the first version of the versioned dataset, with both its documents.

    The documents are removed at the end of fixture.
    """
    search = create_search()
    try:
        if not await search.indices.exists(index=BP_DOMAIN.opensearch_index):
            await _create_index(BP_DOMAIN)
        await _load_and_sync_version(
            [_VERSIONED_EXCLUDED_DOCUMENT_ID, _VERSIONED_INCLUDED_DOCUMENT_ID],
            _FIRST_VERSION,
        )
        yield
        for doc_id in (
            _VERSIONED_EXCLUDED_DOCUMENT_ID,
            _VERSIONED_INCLUDED_DOCUMENT_ID,
        ):
            await search.delete(
                index=BP_DOMAIN.opensearch_index, id=doc_id, ignore=[404]
            )
    finally:
        await search.close()
        await delete_load_marker()


@pytest.mark.asyncio
async def test_newer_version_drops_excluded_document(first_version):
    await _store_log(_VERSIONED_EXCLUDED_DOCUMENT_ID)
    assert await _indexed(_VERSIONED_EXCLUDED_DOCUMENT_ID)

    await _load_and_sync_version([_VERSIONED_INCLUDED_DOCUMENT_ID], _NEWER_VERSION)

    assert not await _stored(_VERSIONED_EXCLUDED_DOCUMENT_ID)
    assert not await _indexed(_VERSIONED_EXCLUDED_DOCUMENT_ID)
    assert not await _log_exists(_VERSIONED_EXCLUDED_DOCUMENT_ID)
    assert await _stored(_VERSIONED_INCLUDED_DOCUMENT_ID)
    assert await _indexed(_VERSIONED_INCLUDED_DOCUMENT_ID)


@pytest.mark.asyncio
async def test_newer_version_rerun_after_sync_prunes(monkeypatch, first_version):
    """A rerun deletes the excluded document from the index.

    The first run stores the newer version, then fails before deleting the excluded
    document from the database. A sync then marks the stored version synced, and
    keeps the excluded document. The rerun deletes it from the database, and must
    make the set pending sync again, so that its sync deletes it from the index.
    """
    with monkeypatch.context() as patched:
        patched.setattr(
            LoadService,
            "_delete_dropped_documents",
            AsyncMock(side_effect=SystemException("dropped_delete_fails")),
        )
        with pytest.raises(SystemException, match="dropped_delete_fails"):
            await _load_and_sync_version(
                [_VERSIONED_INCLUDED_DOCUMENT_ID], _NEWER_VERSION
            )
    assert await _stored(_VERSIONED_EXCLUDED_DOCUMENT_ID)
    await _sync(BP_DOMAIN)
    # Nothing of the set is pending now, so only the rerun's reset gets it pruned.
    assert await _document_is_synced(_VERSIONED_INCLUDED_DOCUMENT_ID)
    assert await _document_is_synced(_VERSIONED_EXCLUDED_DOCUMENT_ID)
    assert await _indexed(_VERSIONED_EXCLUDED_DOCUMENT_ID)

    await _load_and_sync_version([_VERSIONED_INCLUDED_DOCUMENT_ID], _NEWER_VERSION)

    assert not await _stored(_VERSIONED_EXCLUDED_DOCUMENT_ID)
    assert not await _indexed(_VERSIONED_EXCLUDED_DOCUMENT_ID)
    assert await _indexed(_VERSIONED_INCLUDED_DOCUMENT_ID)


@pytest.mark.asyncio
async def test_older_version_drops_nothing(first_version):
    """Reading an older version again must not delete the newer version's documents."""
    await _load_and_sync_version([_VERSIONED_INCLUDED_DOCUMENT_ID], _OLDER_VERSION)

    assert await _stored(_VERSIONED_EXCLUDED_DOCUMENT_ID)
    assert await _indexed(_VERSIONED_EXCLUDED_DOCUMENT_ID)


@pytest.mark.asyncio
async def test_same_version_drops_nothing(first_version):
    await _load_and_sync_version([_VERSIONED_INCLUDED_DOCUMENT_ID], _FIRST_VERSION)

    assert await _stored(_VERSIONED_EXCLUDED_DOCUMENT_ID)
    assert await _indexed(_VERSIONED_EXCLUDED_DOCUMENT_ID)
