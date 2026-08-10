import io
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from crypt4gh.keys import get_public_key as c4gh_get_public_key
from crypt4gh.keys.c4gh import generate as c4gh_generate
from crypt4gh.lib import encrypt as c4gh_encrypt
from nacl.public import PrivateKey

from search_api.api.bigpicture.domain import BP_DOMAIN
from search_api.api.bigpicture.extract import extract_documents
from search_api.database.document import DOCUMENT_TABLE, get_document
from search_api.database.repository import get_connection
from search_api.services.load import LoadService

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

_EXPECTED_DOCUMENTS = {
    **{
        image_id: (_CLINICAL_DATASET_ID, "clinical") for image_id in _CLINICAL_IMAGE_IDS
    },
    **{
        image_id: (_NON_CLINICAL_DATASET_ID, "non_clinical")
        for image_id in _NON_CLINICAL_IMAGE_IDS
    },
}


def _mock_term_caches() -> dict:
    """One mock term cache per ontology in play (keyed like the serving side)."""
    return {
        ontology_id: MagicMock(load=AsyncMock(), cache_preferred_terms=AsyncMock())
        for ontology_id in BP_DOMAIN.ontology_ids
    }


@pytest_asyncio.fixture(autouse=True)
async def delete_images():
    """Delete images before and after each test."""

    async def _delete() -> None:
        async with get_connection() as conn:
            async with conn.cursor() as cur:
                for image_id in _EXPECTED_DOCUMENTS:
                    await cur.execute(
                        f"DELETE FROM {DOCUMENT_TABLE} WHERE id = %s", (image_id,)
                    )

    await _delete()
    yield
    await _delete()


@pytest.mark.asyncio
async def test_extract_and_load_fields_plain():
    """Both clinical and non-clinical datasets are extracted and loaded."""
    await LoadService(
        term_caches=_mock_term_caches(),
        filtering_terms=BP_DOMAIN.filtering_terms,
        filtering_scopes=BP_DOMAIN.filtering_scopes,
        filtering_qualifiers=BP_DOMAIN.filtering_qualifiers,
    ).store_documents(extract_documents(root=str(_XML_DIR), single_dir=False))

    async with get_connection() as conn:
        async with conn.cursor() as cur:
            for image_id, (dataset_id, scope) in _EXPECTED_DOCUMENTS.items():
                payload = await get_document(cur, image_id)
                assert payload is not None, f"{image_id!r} was not loaded"
                assert payload["image_id"] == image_id
                assert payload["dataset_id"] == dataset_id
                assert payload["scope"] == scope


@pytest.mark.asyncio
async def test_extract_and_load_fields_c4gh(tmp_path):
    """Crypt4GH-encrypted XML files are decrypted on the fly and loaded into the database."""
    # Generate a recipient key pair.
    seckey_path = tmp_path / "key.sec"
    pubkey_path = tmp_path / "key.pub"
    c4gh_generate(str(seckey_path), str(pubkey_path), b"", b"")

    recipient_pk = c4gh_get_public_key(str(pubkey_path))
    sender_sk = bytes(PrivateKey.generate())

    # Mirror the clinical dataset directory, replacing each XML with a .c4gh version.
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

    await LoadService(
        term_caches=_mock_term_caches(),
        filtering_terms=BP_DOMAIN.filtering_terms,
        filtering_scopes=BP_DOMAIN.filtering_scopes,
        filtering_qualifiers=BP_DOMAIN.filtering_qualifiers,
    ).store_documents(
        extract_documents(
            root=str(tmp_path),
            single_dir=False,
            c4gh_private_key_file=str(seckey_path),
        )
    )

    async with get_connection() as conn:
        async with conn.cursor() as cur:
            for image_id in _CLINICAL_IMAGE_IDS:
                payload = await get_document(cur, image_id)
                assert payload is not None, f"{image_id!r} was not loaded"
                assert payload["image_id"] == image_id
                assert payload["dataset_id"] == _CLINICAL_DATASET_ID
