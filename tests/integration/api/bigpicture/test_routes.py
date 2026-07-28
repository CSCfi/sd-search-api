"""Integration tests for the API."""

import os
from typing import Any
from urllib.parse import urlparse, urlunparse

import httpx
import pytest
import pytest_asyncio

from search_api.api.beacon.models import (
    BeaconQuery,
    BeaconQueryFilter,
    BeaconQueryRequest,
)
from search_api.api.bigpicture.models import (
    BP_OPENSEARCH_INDEX,
    BigpictureBeaconResultSetsResponse,
)
from search_api.api.models import FieldValue
from search_api.database.repository import get_connection
from search_api.services.ontology_term import SNOMED_TABLE
from tests.integration.mockauth import PORT as OIDC_MOCK_PORT

os.environ.setdefault("POSTGRES_DB", os.environ["BP_POSTGRES_DB"])
os.environ.setdefault("POSTGRES_PORT", os.environ["BP_POSTGRES_PORT"])

DATASET_1 = "dataset_1"
DATASET_2 = "dataset_2"

# animal_species
HUMAN_CONCEPT_ID = "337915000"
MOUSE_CONCEPT_ID = "447612001"

# anatomical_site
BREAST_CONCEPT_ID = "80248007"
PELVIS_CONCEPT_ID = "41216001"
KIDNEY_CONCEPT_ID = "64033007"

# fixation_type
FFPE_CONCEPT_ID = "431510009"
FROZEN_FIX_CONCEPT_ID = "1286895009"

# block_preparation
PARAFFIN_CONCEPT_ID = "311731000"
FROZEN_PREP_CONCEPT_ID = "433469005"

# staining_procedure
HE_CONCEPT_ID = "12710003"  # haematoxylin and eosin
IHC_CONCEPT_ID = "406917005"  # immunohistochemistry
ISH_CONCEPT_ID = "115959002"  # in situ hybridisation

# specimen_type
SPECIMEN_TYPE_CONCEPT_ID = "119376003"

OPENSEARCH_DOCS: list[dict[str, Any]] = [
    {
        "image_id": "image_1",
        "dataset_id": DATASET_1,
        "dataset_image_cnt": 3,
        "dataset_short_name": "Breast-HE",
        "dataset_title": "Human Breast Tissue Collection",
        "dataset_description": "FFPE breast tissue sections stained with haematoxylin and eosin.",
        "specimen": [
            {
                "animal_species": HUMAN_CONCEPT_ID,
                "sex": "Female",
                "anatomical_site": [BREAST_CONCEPT_ID],
                "fixation_type": FFPE_CONCEPT_ID,
                "fixation_type_other": "Formalin",
                "block_preparation": PARAFFIN_CONCEPT_ID,
                "specimen_type": SPECIMEN_TYPE_CONCEPT_ID,
                "age_at_extraction": {"gte": 16425, "lte": 18250},
            }
        ],
        "staining": [
            {
                "staining_procedure": HE_CONCEPT_ID,
                "staining_procedure_other": "Haematoxylin and eosin stain",
            }
        ],
    },
    {
        "image_id": "image_2",
        "dataset_id": DATASET_1,
        "dataset_image_cnt": 3,
        "dataset_short_name": "Breast-HE",
        "dataset_title": "Human Breast Tissue Collection",
        "dataset_description": "FFPE breast tissue sections stained with haematoxylin and eosin.",
        "specimen": [
            {
                "animal_species": HUMAN_CONCEPT_ID,
                "sex": "Female",
                "anatomical_site": [BREAST_CONCEPT_ID],
                "fixation_type": FFPE_CONCEPT_ID,
                "fixation_type_other": "Formalin",
                "block_preparation": PARAFFIN_CONCEPT_ID,
                "specimen_type": SPECIMEN_TYPE_CONCEPT_ID,
                "age_at_extraction": {"gte": 20075, "lte": 21900},
            }
        ],
        "staining": [
            {
                "staining_procedure": IHC_CONCEPT_ID,
                "staining_procedure_other": "Immunohistochemical staining",
                "staining_substance": "antibody",
                "staining_substance_other": "antibody",
                "staining_target": "pan Cytokeratin",
            }
        ],
    },
    {
        "image_id": "image_3",
        "dataset_id": DATASET_1,
        "dataset_image_cnt": 3,
        "dataset_short_name": "Breast-HE",
        "dataset_title": "Human Breast Tissue Collection",
        "dataset_description": "FFPE breast tissue sections stained with haematoxylin and eosin.",
        "specimen": [
            {
                "animal_species": HUMAN_CONCEPT_ID,
                "sex": "Male",
                "anatomical_site": [PELVIS_CONCEPT_ID, KIDNEY_CONCEPT_ID],
                "fixation_type": FFPE_CONCEPT_ID,
                "fixation_type_other": "Formalin",
                "block_preparation": PARAFFIN_CONCEPT_ID,
                "specimen_type": SPECIMEN_TYPE_CONCEPT_ID,
                "age_at_extraction": {"gte": 23725, "lte": 25550},
            }
        ],
        "staining": [
            {
                "staining_procedure": HE_CONCEPT_ID,
                "staining_procedure_other": "Haematoxylin and eosin stain",
            }
        ],
    },
    {
        "image_id": "image_4",
        "dataset_id": DATASET_2,
        "dataset_image_cnt": 2,
        "dataset_short_name": "Mouse-Kidney",
        "dataset_title": "Mouse Kidney Study",
        "dataset_description": "Kidney tissue from Mus musculus prepared by fresh frozen and paraffin embedding.",
        "specimen": [
            {
                "animal_species": MOUSE_CONCEPT_ID,
                "sex": "Male",
                "anatomical_site": [KIDNEY_CONCEPT_ID],
                "fixation_type": FROZEN_FIX_CONCEPT_ID,
                "fixation_type_other": "Custom fix",
                "block_preparation": FROZEN_PREP_CONCEPT_ID,
                "specimen_type": SPECIMEN_TYPE_CONCEPT_ID,
                "age_at_extraction": {"gte": 0, "lte": 365},
            }
        ],
        "staining": [
            {
                "staining_procedure": HE_CONCEPT_ID,
                "staining_procedure_other": "Haematoxylin and eosin stain",
            }
        ],
    },
    {
        "image_id": "image_5",
        "dataset_id": DATASET_2,
        "dataset_image_cnt": 2,
        "dataset_short_name": "Mouse-Kidney",
        "dataset_title": "Mouse Kidney Study",
        "dataset_description": "Kidney tissue from Mus musculus prepared by fresh frozen and paraffin embedding.",
        "specimen": [
            {
                "animal_species": MOUSE_CONCEPT_ID,
                "sex": "Female",
                "anatomical_site": [KIDNEY_CONCEPT_ID],
                "fixation_type": FFPE_CONCEPT_ID,
                "fixation_type_other": "Formalin",
                "block_preparation": PARAFFIN_CONCEPT_ID,
                "specimen_type": SPECIMEN_TYPE_CONCEPT_ID,
                "age_at_extraction": {"gte": 0, "lte": 365},
            }
        ],
        "staining": [
            {
                "staining_procedure": ISH_CONCEPT_ID,
                "staining_procedure_other": "In situ hybridization",
                "staining_substance": "Double-stranded DNA",
                "staining_substance_other": "Double-stranded DNA",
            }
        ],
    },
]

# animal_species preferred terms
HUMAN_PREFERRED_TERM = "Homo sapiens"
MOUSE_PREFERRED_TERM = "Mus musculus"

# anatomical_site preferred terms
BREAST_PREFERRED_TERM = "Breast structure"
PELVIS_PREFERRED_TERM = "Pelvis"
KIDNEY_PREFERRED_TERM = "Kidney"

# fixation_type preferred terms
FFPE_PREFERRED_TERM = "Formalin-fixed paraffin-embedded specimen"
FROZEN_FIX_PREFERRED_TERM = "Frozen specimen"

# specimen_type preferred terms
SPECIMEN_TYPE_PREFERRED_TERM = "Tissue specimen"

# block_preparation preferred terms
PARAFFIN_PREFERRED_TERM = "Paraffin wax"
FROZEN_PREP_PREFERRED_TERM = "Frozen section embedding medium"

# staining_procedure preferred terms
HE_PREFERRED_TERM = "Haematoxylin and eosin stain"
IHC_PREFERRED_TERM = "Immunohistochemistry"
ISH_PREFERRED_TERM = "In situ hybridization"

# SNOMED database and in-memory cache cache for ontology fields in OPENSEARCH_DOCS.
# (concept_id, field_id, preferred_term)
SNOMED_TERMS: list[tuple[str, str, str]] = [
    # animal_species
    (HUMAN_CONCEPT_ID, "animal_species", HUMAN_PREFERRED_TERM),
    (MOUSE_CONCEPT_ID, "animal_species", MOUSE_PREFERRED_TERM),
    # anatomical_site
    (BREAST_CONCEPT_ID, "anatomical_site", BREAST_PREFERRED_TERM),
    (PELVIS_CONCEPT_ID, "anatomical_site", PELVIS_PREFERRED_TERM),
    (KIDNEY_CONCEPT_ID, "anatomical_site", KIDNEY_PREFERRED_TERM),
    # fixation_type
    (FFPE_CONCEPT_ID, "fixation_type", FFPE_PREFERRED_TERM),
    (FROZEN_FIX_CONCEPT_ID, "fixation_type", FROZEN_FIX_PREFERRED_TERM),
    # specimen_type
    (SPECIMEN_TYPE_CONCEPT_ID, "specimen_type", SPECIMEN_TYPE_PREFERRED_TERM),
    # block_preparation
    (PARAFFIN_CONCEPT_ID, "block_preparation", PARAFFIN_PREFERRED_TERM),
    (FROZEN_PREP_CONCEPT_ID, "block_preparation", FROZEN_PREP_PREFERRED_TERM),
    # staining_procedure
    (HE_CONCEPT_ID, "staining_procedure", HE_PREFERRED_TERM),
    (IHC_CONCEPT_ID, "staining_procedure", IHC_PREFERRED_TERM),
    (ISH_CONCEPT_ID, "staining_procedure", ISH_PREFERRED_TERM),
]

_ADMIN_KEY = os.environ.get("ADMIN_KEY", "")


def field_value(value: str, count: int, concept_id: str | None = None) -> FieldValue:
    return FieldValue(value=value, count=count, concept_id=concept_id)


EXPECTED_ONTOLOGY_VALUES: list[tuple[str, list[FieldValue]]] = [
    (
        "animal_species",
        [
            field_value(HUMAN_PREFERRED_TERM, 3, HUMAN_CONCEPT_ID),
            field_value(MOUSE_PREFERRED_TERM, 2, MOUSE_CONCEPT_ID),
        ],
    ),
    (
        "anatomical_site",
        [
            field_value(BREAST_PREFERRED_TERM, 2, BREAST_CONCEPT_ID),
            field_value(PELVIS_PREFERRED_TERM, 1, PELVIS_CONCEPT_ID),
            field_value(KIDNEY_PREFERRED_TERM, 3, KIDNEY_CONCEPT_ID),
        ],
    ),
    (
        "specimen_type",
        [field_value(SPECIMEN_TYPE_PREFERRED_TERM, 5, SPECIMEN_TYPE_CONCEPT_ID)],
    ),
    (
        "block_preparation",
        [
            field_value(PARAFFIN_PREFERRED_TERM, 4, PARAFFIN_CONCEPT_ID),
            field_value(FROZEN_PREP_PREFERRED_TERM, 1, FROZEN_PREP_CONCEPT_ID),
        ],
    ),
    (
        "fixation_type",
        [
            field_value(FFPE_PREFERRED_TERM, 4, FFPE_CONCEPT_ID),
            field_value(FROZEN_FIX_PREFERRED_TERM, 1, FROZEN_FIX_CONCEPT_ID),
        ],
    ),
    (
        "staining_procedure",
        [
            field_value(HE_PREFERRED_TERM, 3, HE_CONCEPT_ID),
            field_value(IHC_PREFERRED_TERM, 1, IHC_CONCEPT_ID),
            field_value(ISH_PREFERRED_TERM, 1, ISH_CONCEPT_ID),
        ],
    ),
]

EXPECTED_ONTOLOGY_SUGGESTIONS: list[tuple[str, str, FieldValue]] = [
    ("animal_species", "homo", field_value(HUMAN_PREFERRED_TERM, 3, HUMAN_CONCEPT_ID)),
    ("animal_species", "mus", field_value(MOUSE_PREFERRED_TERM, 2, MOUSE_CONCEPT_ID)),
    (
        "anatomical_site",
        "breast",
        field_value(BREAST_PREFERRED_TERM, 2, BREAST_CONCEPT_ID),
    ),
    (
        "anatomical_site",
        "kidney",
        field_value(KIDNEY_PREFERRED_TERM, 3, KIDNEY_CONCEPT_ID),
    ),
    (
        "specimen_type",
        "tissue",
        field_value(SPECIMEN_TYPE_PREFERRED_TERM, 5, SPECIMEN_TYPE_CONCEPT_ID),
    ),
    (
        "block_preparation",
        "paraffin",
        field_value(PARAFFIN_PREFERRED_TERM, 4, PARAFFIN_CONCEPT_ID),
    ),
    (
        "block_preparation",
        "frozen",
        field_value(FROZEN_PREP_PREFERRED_TERM, 1, FROZEN_PREP_CONCEPT_ID),
    ),
    ("fixation_type", "formalin", field_value(FFPE_PREFERRED_TERM, 4, FFPE_CONCEPT_ID)),
    ("staining_procedure", "haema", field_value(HE_PREFERRED_TERM, 3, HE_CONCEPT_ID)),
    (
        "staining_procedure",
        "immunoh",
        field_value(IHC_PREFERRED_TERM, 1, IHC_CONCEPT_ID),
    ),
]

EXPECTED_ONTOLOGY_OTHER_VALUES: list[tuple[str, list[FieldValue]]] = [
    (
        "fixation_type",
        [field_value("Formalin", 4), field_value("Custom fix", 1)],
    ),
    (
        "staining_procedure",
        [
            field_value("Haematoxylin and eosin stain", 3),
            field_value("Immunohistochemical staining", 1),
            field_value("In situ hybridization", 1),
        ],
    ),
]

EXPECTED_ONTOLOGY_OTHER_SUGGESTIONS: list[tuple[str, str, FieldValue]] = [
    ("fixation_type", "custom", field_value("Custom fix", 1)),
    ("staining_procedure", "immunoh", field_value("Immunohistochemical staining", 1)),
]


@pytest.fixture(scope="module")
def bp_opensearch_docs() -> list[dict[str, Any]]:
    return OPENSEARCH_DOCS


@pytest.fixture(scope="module")
def bp_opensearch_index_name() -> str:
    return BP_OPENSEARCH_INDEX


@pytest.fixture(scope="module")
def client() -> httpx.Client:
    with httpx.Client(
        base_url="http://localhost:8000", follow_redirects=False, timeout=30.0
    ) as c:
        # Step 1: Initiate login - store the oidc_state cookie and get the IdP auth URL.
        login_resp = c.get("/login")
        assert login_resp.status_code == 303
        auth_url = login_resp.headers["location"]

        # Step 2: The auth URL may use the docker-network hostname (mockauth:8998),
        # which isn't resolvable from the test host. Rewrite to 127.0.0.1 for the
        # host-accessible port binding.
        parsed_auth = urlparse(auth_url)
        host_auth_url = urlunparse(
            parsed_auth._replace(netloc=f"127.0.0.1:{OIDC_MOCK_PORT}")
        )

        # Step 3: Follow the IdP /authorize - mock immediately redirects to /callback.
        oidc_resp = httpx.get(host_auth_url, follow_redirects=False)
        assert oidc_resp.status_code == 303
        callback_location = oidc_resp.headers["location"]

        # Step 4: Follow /callback on the API (uses relative path so the session client
        # sends the oidc_state cookie it received in step 1).
        parsed_cb = urlparse(callback_location)
        callback_path = parsed_cb.path + (
            "?" + parsed_cb.query if parsed_cb.query else ""
        )
        final_resp = c.get(callback_path, follow_redirects=True)
        assert final_resp.status_code == 200
        assert c.cookies.get("access_token") is not None
        yield c


@pytest_asyncio.fixture(scope="module")
async def snomed_terms(client: httpx.Client):
    """Initialise database SNOMED preferred terms cache."""

    # Insert values to database SNOMED preferred terms cache.
    async with get_connection() as conn:
        async with conn.cursor() as cur:
            await cur.executemany(
                f"INSERT INTO {SNOMED_TABLE} (concept_id, field_id, preferred_term, updated_at)"
                " VALUES (%s, %s, %s, now())"
                " ON CONFLICT (concept_id, field_id) DO UPDATE SET preferred_term = EXCLUDED.preferred_term,"
                " updated_at = now()",
                SNOMED_TERMS,
            )

    # Reload in-memory cache.
    resp = client.post(
        "/admin/snomed/reload", headers={"Authorization": f"Bearer {_ADMIN_KEY}"}
    )
    assert resp.status_code == 204

    yield

    # Delete values from the database SNOMED preferred terms cache.
    async with get_connection() as conn:
        async with conn.cursor() as cur:
            await cur.executemany(
                f"DELETE FROM {SNOMED_TABLE} WHERE concept_id = %s AND field_id = %s",
                [(concept_id, field_id) for concept_id, field_id, _ in SNOMED_TERMS],
            )


def get_filters(
    *field_id_value_pairs: tuple[str, str | list[str]],
) -> list[BeaconQueryFilter]:
    return [
        BeaconQueryFilter(id=field_id, value=value, includeDescendantTerms=False)
        for field_id, value in field_id_value_pairs
    ]


def query(
    client: httpx.Client, *field_id_value_pairs: tuple[str, str | list[str]]
) -> BigpictureBeaconResultSetsResponse:
    request = BeaconQueryRequest(
        query=BeaconQuery(
            filters=get_filters(*field_id_value_pairs),
            requestedGranularity="record",
        )
    )
    resp = client.post("/query", json=request.model_dump())
    assert resp.status_code == 200
    return BigpictureBeaconResultSetsResponse.model_validate(resp.json())


def get_dataset_ids(response: BigpictureBeaconResultSetsResponse) -> set[str]:
    return {rs.id for rs in response.response.resultSet}


def get_matching_image_count(
    response: BigpictureBeaconResultSetsResponse, dataset_id: str
) -> int:
    for rs in response.response.resultSet:
        if rs.id == dataset_id:
            return rs.results[0].matchingImageCount
    return 0


def get_dataset_url(
    response: BigpictureBeaconResultSetsResponse, dataset_id: str
) -> str | None:
    for rs in response.response.resultSet:
        if rs.id == dataset_id:
            return rs.results[0].datasetUrl
    return None


@pytest.mark.asyncio
async def test_query_no_filters_returns_all_datasets(bp_opensearch_index, client):
    result = query(client)
    assert get_dataset_ids(result) == {DATASET_1, DATASET_2}
    assert get_matching_image_count(result, DATASET_1) == 3
    assert get_matching_image_count(result, DATASET_2) == 2
    assert get_dataset_url(result, DATASET_1) == (
        f"https://datasets.bigipicture.eu/datasets/{DATASET_1}.html"
    )
    assert get_dataset_url(result, DATASET_2) == (
        f"https://datasets.bigipicture.eu/datasets/{DATASET_2}.html"
    )


# Species
#


@pytest.mark.asyncio
async def test_query_filter_human_species(bp_opensearch_index, client):
    result = query(client, ("animal_species", HUMAN_CONCEPT_ID))
    assert get_dataset_ids(result) == {DATASET_1}
    assert get_matching_image_count(result, DATASET_1) == 3


@pytest.mark.asyncio
async def test_query_filter_mouse_species(bp_opensearch_index, client):
    result = query(client, ("animal_species", MOUSE_CONCEPT_ID))
    assert get_dataset_ids(result) == {DATASET_2}
    assert get_matching_image_count(result, DATASET_2) == 2


@pytest.mark.asyncio
async def test_query_filter_both_species(bp_opensearch_index, client):
    result = query(client, ("animal_species", [HUMAN_CONCEPT_ID, MOUSE_CONCEPT_ID]))
    assert get_dataset_ids(result) == {DATASET_1, DATASET_2}


# Sex
#


@pytest.mark.asyncio
async def test_query_filter_sex_female(bp_opensearch_index, client):
    result = query(client, ("sex", "Female"))
    assert get_dataset_ids(result) == {DATASET_1, DATASET_2}
    assert get_matching_image_count(result, DATASET_1) == 2  # image_1, image_2
    assert get_matching_image_count(result, DATASET_2) == 1  # image_5


@pytest.mark.asyncio
async def test_query_filter_sex_male(bp_opensearch_index, client):
    result = query(client, ("sex", "Male"))
    assert get_dataset_ids(result) == {DATASET_1, DATASET_2}
    assert get_matching_image_count(result, DATASET_1) == 1  # image_3
    assert get_matching_image_count(result, DATASET_2) == 1  # image_4


# Anatomical site
#


@pytest.mark.asyncio
async def test_query_filter_anatomical_site_breast(bp_opensearch_index, client):
    result = query(client, ("anatomical_site", BREAST_CONCEPT_ID))
    assert get_dataset_ids(result) == {DATASET_1}
    assert get_matching_image_count(result, DATASET_1) == 2  # image_1, image_2


@pytest.mark.asyncio
async def test_query_filter_anatomical_site_kidney(bp_opensearch_index, client):
    result = query(client, ("anatomical_site", KIDNEY_CONCEPT_ID))
    assert get_dataset_ids(result) == {DATASET_1, DATASET_2}
    assert get_matching_image_count(result, DATASET_1) == 1  # image_3
    assert get_matching_image_count(result, DATASET_2) == 2


# Fixation type
#


@pytest.mark.asyncio
async def test_query_filter_fixation_ffpe(bp_opensearch_index, client):
    result = query(client, ("fixation_type", FFPE_CONCEPT_ID))
    assert get_dataset_ids(result) == {DATASET_1, DATASET_2}
    assert get_matching_image_count(result, DATASET_1) == 3
    assert get_matching_image_count(result, DATASET_2) == 1  # image_5


@pytest.mark.asyncio
async def test_query_filter_fixation_frozen(bp_opensearch_index, client):
    result = query(client, ("fixation_type", FROZEN_FIX_CONCEPT_ID))
    assert get_dataset_ids(result) == {DATASET_2}
    assert get_matching_image_count(result, DATASET_2) == 1  # image_4


# Block preparation
#


@pytest.mark.asyncio
async def test_query_filter_block_preparation_paraffin(bp_opensearch_index, client):
    result = query(client, ("block_preparation", PARAFFIN_CONCEPT_ID))
    assert get_dataset_ids(result) == {DATASET_1, DATASET_2}
    assert get_matching_image_count(result, DATASET_1) == 3
    assert get_matching_image_count(result, DATASET_2) == 1  # image_5


@pytest.mark.asyncio
async def test_query_filter_block_preparation_frozen(bp_opensearch_index, client):
    result = query(client, ("block_preparation", FROZEN_PREP_CONCEPT_ID))
    assert get_dataset_ids(result) == {DATASET_2}
    assert get_matching_image_count(result, DATASET_2) == 1  # image_4


# Staining procedure
#


@pytest.mark.asyncio
async def test_query_filter_staining_he(bp_opensearch_index, client):
    result = query(client, ("staining_procedure", HE_CONCEPT_ID))
    assert get_dataset_ids(result) == {DATASET_1, DATASET_2}
    assert get_matching_image_count(result, DATASET_1) == 2  # image_1, image_3
    assert get_matching_image_count(result, DATASET_2) == 1  # image_4


@pytest.mark.asyncio
async def test_query_filter_staining_ihc(bp_opensearch_index, client):
    result = query(client, ("staining_procedure", IHC_CONCEPT_ID))
    assert get_dataset_ids(result) == {DATASET_1}
    assert get_matching_image_count(result, DATASET_1) == 1  # image_2


@pytest.mark.asyncio
async def test_query_filter_staining_ish(bp_opensearch_index, client):
    result = query(client, ("staining_procedure", ISH_CONCEPT_ID))
    assert get_dataset_ids(result) == {DATASET_2}
    assert get_matching_image_count(result, DATASET_2) == 1  # image_5


@pytest.mark.asyncio
async def test_query_filter_staining_he_and_ihc(bp_opensearch_index, client):
    result = query(client, ("staining_procedure", [HE_CONCEPT_ID, IHC_CONCEPT_ID]))
    assert get_dataset_ids(result) == {DATASET_1, DATASET_2}
    assert (
        get_matching_image_count(result, DATASET_1) == 3
    )  # all three match HE_CONCEPT_ID or IHC_CONCEPT_ID
    assert get_matching_image_count(result, DATASET_2) == 1  # image_4 (HE_CONCEPT_ID)


# Staining substance
#


@pytest.mark.asyncio
async def test_query_filter_staining_substance(bp_opensearch_index, client):
    result = query(client, ("staining_substance", "antibody"))
    assert get_dataset_ids(result) == {DATASET_1}
    assert get_matching_image_count(result, DATASET_1) == 1  # image_2


# Age at extraction
#


@pytest.mark.asyncio
async def test_query_filter_age_45_to_55_years(bp_opensearch_index, client):
    # P45Y=16425d, P55Y=20075d — intersects image_1 (16425-18250) and image_2 (20075-21900).
    result = query(client, ("age_at_extraction", "P45Y-P55Y"))
    assert get_dataset_ids(result) == {DATASET_1}
    assert get_matching_image_count(result, DATASET_1) == 2


@pytest.mark.asyncio
async def test_query_filter_age_65_to_75_years(bp_opensearch_index, client):
    # P65Y=23725d, P75Y=27375d — intersects only image_3 (23725-25550).
    result = query(client, ("age_at_extraction", "P65Y-P75Y"))
    assert get_dataset_ids(result) == {DATASET_1}
    assert get_matching_image_count(result, DATASET_1) == 1


@pytest.mark.asyncio
async def test_query_filter_age_range_no_match(bp_opensearch_index, client):
    # P80Y=29200d — beyond all ages in the fixture.
    result = query(client, ("age_at_extraction", "P80Y-P90Y"))
    assert get_dataset_ids(result) == set()


# Dataset title
#


@pytest.mark.asyncio
async def test_query_filter_dataset_title_text(bp_opensearch_index, client):
    result = query(client, ("dataset_title", "Breast"))
    assert get_dataset_ids(result) == {DATASET_1}
    assert get_matching_image_count(result, DATASET_1) == 3


# Dataset description
#


@pytest.mark.asyncio
async def test_query_filter_dataset_description_text(bp_opensearch_index, client):
    result = query(client, ("dataset_description", "frozen"))
    assert get_dataset_ids(result) == {DATASET_2}


# Combined filters
#


@pytest.mark.asyncio
async def test_query_combined_species_and_staining(bp_opensearch_index, client):
    result = query(
        client,
        ("animal_species", HUMAN_CONCEPT_ID),
        ("staining_procedure", HE_CONCEPT_ID),
    )
    assert get_dataset_ids(result) == {DATASET_1}
    assert get_matching_image_count(result, DATASET_1) == 2  # image_1, image_3


@pytest.mark.asyncio
async def test_query_combined_species_and_anatomical_site(bp_opensearch_index, client):
    result = query(
        client,
        ("animal_species", MOUSE_CONCEPT_ID),
        ("anatomical_site", KIDNEY_CONCEPT_ID),
    )
    assert get_dataset_ids(result) == {DATASET_2}
    assert get_matching_image_count(result, DATASET_2) == 2


@pytest.mark.asyncio
async def test_query_combined_no_match(bp_opensearch_index, client):
    # Human species + ISH_CONCEPT_ID staining (only in mouse dataset) → no results.
    result = query(
        client,
        ("animal_species", HUMAN_CONCEPT_ID),
        ("staining_procedure", ISH_CONCEPT_ID),
    )
    assert get_dataset_ids(result) == set()


# Response granularity
#


@pytest.mark.asyncio
async def test_query_boolean_granularity(bp_opensearch_index, client):
    request = BeaconQueryRequest(
        query=BeaconQuery(
            filters=get_filters(("animal_species", HUMAN_CONCEPT_ID)),
            requestedGranularity="boolean",
        )
    )
    resp = client.post("/query", json=request.model_dump())
    assert resp.status_code == 200
    assert resp.json()["responseSummary"]["exists"] is True


@pytest.mark.asyncio
async def test_query_count_granularity(bp_opensearch_index, client):
    request = BeaconQueryRequest(
        query=BeaconQuery(
            filters=get_filters(("animal_species", HUMAN_CONCEPT_ID)),
            requestedGranularity="count",
        )
    )
    resp = client.post("/query", json=request.model_dump())
    assert resp.status_code == 200
    data = resp.json()
    assert data["responseSummary"]["exists"] is True
    assert data["responseSummary"]["numTotalResults"] == 1  # 1 matching dataset


# /values with controlled value (sex)
#


@pytest.mark.asyncio
async def test_values_sex(bp_opensearch_index, client):
    resp = client.get("/filtering_terms/sex/values")
    assert resp.status_code == 200
    results = [FieldValue.model_validate(r) for r in resp.json()]
    counts = {r.value: r.count for r in results}
    assert counts == {"Female": 3, "Male": 2}


@pytest.mark.asyncio
async def test_values_sex_include_all_controlled_values(bp_opensearch_index, client):
    resp = client.get(
        "/filtering_terms/sex/values", params={"include_all_controlled_values": True}
    )
    assert resp.status_code == 200
    results = [FieldValue.model_validate(r) for r in resp.json()]
    counts = {r.value: r.count for r in results}
    assert counts["Female"] == 3
    assert counts["Male"] == 2
    assert counts["Not-known"] == 0
    assert counts["Other"] == 0


@pytest.mark.asyncio
async def test_values_unknown_field(bp_opensearch_index, client):
    resp = client.get("/filtering_terms/unknown/values")
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_values_unsupported_type(bp_opensearch_index, client):
    resp = client.get("/filtering_terms/dataset_title/values")
    assert resp.status_code == 400


# /suggestions with controlled value (sex)
#


@pytest.mark.asyncio
async def test_suggestions_sex_prefix_match(bp_opensearch_index, client):
    resp = client.get("/filtering_terms/sex/suggestions", params={"term": "fe"})
    assert resp.status_code == 200
    results = [FieldValue.model_validate(r) for r in resp.json()]
    assert results == [field_value("Female", 3)]


@pytest.mark.asyncio
async def test_suggestions_sex_no_match(bp_opensearch_index, client):
    resp = client.get("/filtering_terms/sex/suggestions", params={"term": "xyz"})
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_suggestions_sex_include_all_controlled_values(
    bp_opensearch_index, client
):
    resp = client.get(
        "/filtering_terms/sex/suggestions",
        params={"term": "not", "include_all_controlled_values": True},
    )
    assert resp.status_code == 200
    results = [FieldValue.model_validate(r) for r in resp.json()]
    assert field_value("Not-known", 0) in results


@pytest.mark.asyncio
async def test_suggestions_sex_substring_match(bp_opensearch_index, client):
    resp = client.get(
        "/filtering_terms/sex/suggestions",
        params={"term": "ale", "substring_match": True},
    )
    assert resp.status_code == 200
    results = [FieldValue.model_validate(r) for r in resp.json()]
    assert field_value("Male", 2) in results
    assert field_value("Female", 3) in results


@pytest.mark.asyncio
async def test_suggestions_unknown_field(bp_opensearch_index, client):
    resp = client.get("/filtering_terms/unknown/suggestions", params={"term": "x"})
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_suggestions_unsupported_type(bp_opensearch_index, client):
    resp = client.get(
        "/filtering_terms/dataset_title/suggestions", params={"term": "x"}
    )
    assert resp.status_code == 400


# /values and /suggestions for ontology and ontologyOrValue fields
#


@pytest.mark.asyncio
@pytest.mark.parametrize("field_id,expected", EXPECTED_ONTOLOGY_VALUES)
async def test_values_ontology_fields(
    bp_opensearch_index, snomed_terms, client, field_id, expected
):
    resp = client.get(f"/filtering_terms/{field_id}/values")
    assert resp.status_code == 200
    results = [FieldValue.model_validate(r) for r in resp.json()]
    for ev in expected:
        assert ev in results


@pytest.mark.asyncio
@pytest.mark.parametrize("field_id,expected", EXPECTED_ONTOLOGY_OTHER_VALUES)
async def test_other_values_ontology_fields(
    bp_opensearch_index, snomed_terms, client, field_id, expected
):
    resp = client.get(
        f"/filtering_terms/{field_id}/values",
        params={"include_other_ontology_values": True},
    )
    assert resp.status_code == 200
    results = [FieldValue.model_validate(r) for r in resp.json()]
    for ev in expected:
        assert ev in results


@pytest.mark.asyncio
@pytest.mark.parametrize("field_id,term,expected", EXPECTED_ONTOLOGY_SUGGESTIONS)
async def test_suggestions_ontology_fields(
    bp_opensearch_index, snomed_terms, client, field_id, term, expected
):
    resp = client.get(f"/filtering_terms/{field_id}/suggestions", params={"term": term})
    assert resp.status_code == 200
    results = [FieldValue.model_validate(r) for r in resp.json()]
    assert expected in results


@pytest.mark.asyncio
@pytest.mark.parametrize("field_id,term,expected", EXPECTED_ONTOLOGY_OTHER_SUGGESTIONS)
async def test_other_suggestions_ontology_fields(
    bp_opensearch_index, snomed_terms, client, field_id, term, expected
):
    resp = client.get(
        f"/filtering_terms/{field_id}/suggestions",
        params={"term": term, "include_other_ontology_values": True},
    )
    assert resp.status_code == 200
    results = [FieldValue.model_validate(r) for r in resp.json()]
    assert expected in results


@pytest.mark.asyncio
async def test_suggestions_no_match(bp_opensearch_index, snomed_terms, client):
    resp = client.get(
        "/filtering_terms/animal_species/suggestions", params={"term": "xyz"}
    )
    assert resp.status_code == 200
    assert resp.json() == []
