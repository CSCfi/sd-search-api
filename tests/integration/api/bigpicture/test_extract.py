import io
import os
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from crypt4gh.keys import get_public_key as c4gh_get_public_key
from crypt4gh.keys.c4gh import generate as c4gh_generate
from crypt4gh.lib import encrypt as c4gh_encrypt
from nacl.public import PrivateKey

from search_api.api.beacon.models import BeaconFilteringTerm, OntologyRestriction
from search_api.api.bigpicture.domain import BP_DOMAIN
from search_api.services.ontology.service import (
    OntologyService,
    get_ontology_service,
    register_ontology_service,
)
from search_api.services.ontology.term_cache import OntologyTermCache
from search_api.api.bigpicture.local import BigpictureLocalSource
from search_api.database.document import get_document
from search_api.database.repository import get_connection
from search_api.services.load import LoadService

from tests.integration.bigpicture import (
    assert_dataset_images_stored,
    delete_dataset_images,
)
from tests.utils.bigpicture import (
    CLINICAL_2_0_DATASET_DIR,
    CLINICAL_2_0_DATASET_ID,
    CLINICAL_2_0_IMAGE_IDS,
    VERSION_DIRS,
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


async def _documents(root: str, c4gh_key_file: str | None = None) -> Iterator:
    if c4gh_key_file is not None:
        os.environ["C4GH_KEY_FILE"] = c4gh_key_file
    else:
        os.environ.pop("C4GH_KEY_FILE", None)

    documents = [
        document
        async for source in BigpictureLocalSource().read(root)
        for document in source.documents
    ]
    return iter(documents)


def _mock_term_caches() -> dict[str, OntologyTermCache]:
    """One mock term cache per ontology.

    Reports every concept id as known, and no term as cached, so a value that is not
    a concept id is resolved against the ontology rather than out of the cache.
    """
    return {
        ontology_id: MagicMock(
            spec=OntologyTermCache,
            load=AsyncMock(),
            cache_preferred_terms=AsyncMock(),
            get_concept_ids_by_term=AsyncMock(return_value=set()),
        )
        for ontology_id in BP_DOMAIN.ontology_ids
    }


class _MockOntologyService(OntologyService):
    """An ontology that accepts every concept id and resolves nothing."""

    def is_well_formed(self, concept_id: str) -> bool:
        return True

    def is_concept_id_prefix(self, value: str) -> bool:
        return True

    async def is_known(self, concept_id: str) -> bool:
        return True

    async def get_preferred_terms(self, concept_ids: set[str]) -> dict[str, str]:
        return {}

    async def _find_concept_ids(
        self, value: str, filtering_term: BeaconFilteringTerm
    ) -> set[str]:
        return set()

    async def _find_descendant_ids(self, concept_ids: set[str]) -> set[str]:
        return set()

    async def _is_within_restriction(
        self, concept_id: str, restriction: OntologyRestriction
    ) -> bool:
        return True


@pytest.fixture(autouse=True)
def mock_ontologies():
    originals = {
        ontology_id: get_ontology_service(ontology_id)
        for ontology_id in BP_DOMAIN.ontology_ids
    }
    for ontology_id in originals:
        register_ontology_service(ontology_id, _MockOntologyService())
    yield
    for ontology_id, service in originals.items():
        register_ontology_service(ontology_id, service)


@pytest_asyncio.fixture(autouse=True)
async def delete_images():
    """Delete images before and after each test."""

    async def _delete() -> None:
        async with get_connection() as conn:
            async with conn.cursor() as cur:
                await delete_dataset_images(cur)

    await _delete()
    yield
    await _delete()


@pytest.mark.asyncio
async def test_extract_and_load_fields_plain():
    """Clinical and non-clinical datasets of every metadata standard version are loaded."""
    load_service = LoadService(
        term_caches=_mock_term_caches(),
        filtering_terms=BP_DOMAIN.filtering_terms,
        filtering_scopes=BP_DOMAIN.filtering_scopes,
    )
    for version_dir in VERSION_DIRS:
        await load_service.store_documents(await _documents(str(version_dir)))

    async with get_connection() as conn:
        async with conn.cursor() as cur:
            await assert_dataset_images_stored(cur)


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

    await LoadService(
        term_caches=_mock_term_caches(),
        filtering_terms=BP_DOMAIN.filtering_terms,
        filtering_scopes=BP_DOMAIN.filtering_scopes,
    ).store_documents(await _documents(str(tmp_path), c4gh_key_file=str(seckey_path)))

    async with get_connection() as conn:
        async with conn.cursor() as cur:
            for image_id in CLINICAL_2_0_IMAGE_IDS:
                payload = await get_document(cur, image_id)
                assert payload is not None, f"{image_id!r} was not loaded"
                assert payload["image_id"] == image_id
                assert payload["dataset_id"] == CLINICAL_2_0_DATASET_ID
