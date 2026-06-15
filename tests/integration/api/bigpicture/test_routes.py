"""Integration tests for the API."""

import os
from typing import Any

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
    BP_SNOMED_TABLE,
    BigpictureBeaconResultSetsResponse,
)
from search_api.api.models import FieldValue
from search_api.database.repository import get_connection

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
        "blocks": [
            {
                "species": HUMAN_CONCEPT_ID,
                "sex": "Female",
                "anatomical_site": [BREAST_CONCEPT_ID],
                "fixation_type": FFPE_CONCEPT_ID,
                "fixation_type_text": "Formalin",
                "block_preparation": PARAFFIN_CONCEPT_ID,
                "specimen_type": SPECIMEN_TYPE_CONCEPT_ID,
                "age_at_extraction": {"gte": 16425, "lte": 18250},
            }
        ],
        "stains": [
            {
                "staining_procedure": HE_CONCEPT_ID,
                "staining_procedure_text": "Haematoxylin and eosin stain",
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
        "blocks": [
            {
                "species": HUMAN_CONCEPT_ID,
                "sex": "Female",
                "anatomical_site": [BREAST_CONCEPT_ID],
                "fixation_type": FFPE_CONCEPT_ID,
                "fixation_type_text": "Formalin",
                "block_preparation": PARAFFIN_CONCEPT_ID,
                "specimen_type": SPECIMEN_TYPE_CONCEPT_ID,
                "age_at_extraction": {"gte": 20075, "lte": 21900},
            }
        ],
        "stains": [
            {
                "staining_procedure": IHC_CONCEPT_ID,
                "staining_procedure_text": "Immunohistochemical staining",
                "staining_substance": "antibody",
                "staining_substance_text": "antibody",
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
        "blocks": [
            {
                "species": HUMAN_CONCEPT_ID,
                "sex": "Male",
                "anatomical_site": [PELVIS_CONCEPT_ID, KIDNEY_CONCEPT_ID],
                "fixation_type": FFPE_CONCEPT_ID,
                "fixation_type_text": "Formalin",
                "block_preparation": PARAFFIN_CONCEPT_ID,
                "specimen_type": SPECIMEN_TYPE_CONCEPT_ID,
                "age_at_extraction": {"gte": 23725, "lte": 25550},
            }
        ],
        "stains": [
            {
                "staining_procedure": HE_CONCEPT_ID,
                "staining_procedure_text": "Haematoxylin and eosin stain",
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
        "blocks": [
            {
                "species": MOUSE_CONCEPT_ID,
                "sex": "Male",
                "anatomical_site": [KIDNEY_CONCEPT_ID],
                "fixation_type": FROZEN_FIX_CONCEPT_ID,
                "fixation_type_text": "Custom fix",
                "block_preparation": FROZEN_PREP_CONCEPT_ID,
                "specimen_type": SPECIMEN_TYPE_CONCEPT_ID,
                "age_at_extraction": {"gte": 0, "lte": 365},
            }
        ],
        "stains": [
            {
                "staining_procedure": HE_CONCEPT_ID,
                "staining_procedure_text": "Haematoxylin and eosin stain",
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
        "blocks": [
            {
                "species": MOUSE_CONCEPT_ID,
                "sex": "Female",
                "anatomical_site": [KIDNEY_CONCEPT_ID],
                "fixation_type": FFPE_CONCEPT_ID,
                "fixation_type_text": "Formalin",
                "block_preparation": PARAFFIN_CONCEPT_ID,
                "specimen_type": SPECIMEN_TYPE_CONCEPT_ID,
                "age_at_extraction": {"gte": 0, "lte": 365},
            }
        ],
        "stains": [
            {
                "staining_procedure": ISH_CONCEPT_ID,
                "staining_procedure_text": "In situ hybridization",
                "staining_substance": "Double-stranded DNA",
                "staining_substance_text": "Double-stranded DNA",
            }
        ],
    },
]

# animal_species preferred terms
HUMAN_PREFERRED_TERM = "Homo sapiens"
MOUSE_PREFERRED_TERM = "Mus musculus"

# fixation_type preferred terms
FFPE_PREFERRED_TERM = "Formalin-fixed paraffin-embedded specimen"
FROZEN_FIX_PREFERRED_TERM = "Frozen specimen"

# Preferred terms seeded into the SNOMED cache for animal_species and fixation_type
# /values and /suggestions tests. Other ontology fields in OPENSEARCH_DOCS are not covered.
SNOMED_TERMS: dict[str, str] = {
    # animal_species
    HUMAN_CONCEPT_ID: HUMAN_PREFERRED_TERM,
    MOUSE_CONCEPT_ID: MOUSE_PREFERRED_TERM,
    # fixation_type
    FFPE_CONCEPT_ID: FFPE_PREFERRED_TERM,
    FROZEN_FIX_CONCEPT_ID: FROZEN_FIX_PREFERRED_TERM,
}

_ADMIN_KEY = os.environ.get("ADMIN_KEY", "")


@pytest.fixture(scope="module")
def bp_opensearch_docs() -> list[dict[str, Any]]:
    return OPENSEARCH_DOCS


@pytest.fixture(scope="module")
def bp_opensearch_index_name() -> str:
    return BP_OPENSEARCH_INDEX


@pytest.fixture(scope="module")
def client() -> httpx.Client:
    with httpx.Client(base_url="http://localhost:8000") as c:
        yield c


@pytest_asyncio.fixture(scope="module")
async def snomed_terms(client: httpx.Client):
    """Initialise database SNOMED preferred terms cache."""

    # Insert values to database SNOMED preferred terms cache.
    rows = list(SNOMED_TERMS.items())
    async with get_connection() as conn:
        async with conn.cursor() as cur:
            await cur.executemany(
                f"INSERT INTO {BP_SNOMED_TABLE} (concept_id, preferred_term, updated_at)"
                " VALUES (%s, %s, now())"
                " ON CONFLICT (concept_id) DO UPDATE SET preferred_term = EXCLUDED.preferred_term,"
                " updated_at = now()",
                rows,
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
            await cur.execute(
                f"DELETE FROM {BP_SNOMED_TABLE} WHERE concept_id = ANY(%s)",
                (list(SNOMED_TERMS.keys()),),
            )


def field_value(value: str, count: int, concept_id: str | None = None) -> FieldValue:
    return FieldValue(value=value, count=count, concept_id=concept_id)


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


@pytest.mark.asyncio
async def test_query_no_filters_returns_all_datasets(bp_opensearch_index, client):
    result = query(client)
    assert get_dataset_ids(result) == {DATASET_1, DATASET_2}
    assert get_matching_image_count(result, DATASET_1) == 3
    assert get_matching_image_count(result, DATASET_2) == 2


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
    assert resp.status_code == 404


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
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_suggestions_unsupported_type(bp_opensearch_index, client):
    resp = client.get(
        "/filtering_terms/dataset_title/suggestions", params={"term": "x"}
    )
    assert resp.status_code == 400


# /values with ontology value (animal_species)
# Requires the SNOMED database and in-memory cache to be updated.
#


@pytest.mark.asyncio
async def test_values_animal_species(bp_opensearch_index, snomed_terms, client):
    resp = client.get("/filtering_terms/animal_species/values")
    assert resp.status_code == 200
    results = [FieldValue.model_validate(r) for r in resp.json()]
    assert field_value(HUMAN_PREFERRED_TERM, 3, HUMAN_CONCEPT_ID) in results
    assert field_value(MOUSE_PREFERRED_TERM, 2, MOUSE_CONCEPT_ID) in results


# /suggestions with ontology value (animal_species)
# Requires the SNOMED database and in-memory cache to be updated.
#


@pytest.mark.asyncio
async def test_suggestions_animal_species_prefix(
    bp_opensearch_index, snomed_terms, client
):
    resp = client.get(
        "/filtering_terms/animal_species/suggestions", params={"term": "homo"}
    )
    assert resp.status_code == 200
    results = [FieldValue.model_validate(r) for r in resp.json()]
    assert results == [field_value(HUMAN_PREFERRED_TERM, 3, HUMAN_CONCEPT_ID)]


@pytest.mark.asyncio
async def test_suggestions_animal_species_no_match(
    bp_opensearch_index, snomed_terms, client
):
    resp = client.get(
        "/filtering_terms/animal_species/suggestions", params={"term": "xyz"}
    )
    assert resp.status_code == 200
    assert resp.json() == []


# /values — ontologyOrValue (fixation_type)
#


@pytest.mark.asyncio
async def test_values_fixation_type(bp_opensearch_index, snomed_terms, client):
    resp = client.get("/filtering_terms/fixation_type/values")
    assert resp.status_code == 200
    results = [FieldValue.model_validate(r) for r in resp.json()]
    assert field_value(FFPE_PREFERRED_TERM, 4, FFPE_CONCEPT_ID) in results
    assert field_value(FROZEN_FIX_PREFERRED_TERM, 1, FROZEN_FIX_CONCEPT_ID) in results


@pytest.mark.asyncio
async def test_values_fixation_type_include_other_ontology_values(
    bp_opensearch_index, snomed_terms, client
):
    resp = client.get(
        "/filtering_terms/fixation_type/values",
        params={"include_other_ontology_values": True},
    )
    assert resp.status_code == 200
    results = [FieldValue.model_validate(r) for r in resp.json()]
    assert field_value("Formalin", 4) in results
    assert field_value("Custom fix", 1) in results


# /suggestions — ontologyOrValue (fixation_type)
#


@pytest.mark.asyncio
async def test_suggestions_fixation_type(bp_opensearch_index, snomed_terms, client):
    resp = client.get(
        "/filtering_terms/fixation_type/suggestions", params={"term": "formalin"}
    )
    assert resp.status_code == 200
    results = [FieldValue.model_validate(r) for r in resp.json()]
    assert field_value(FFPE_PREFERRED_TERM, 4, FFPE_CONCEPT_ID) in results


@pytest.mark.asyncio
async def test_suggestions_fixation_type_include_other_ontology_values(
    bp_opensearch_index, snomed_terms, client
):
    resp = client.get(
        "/filtering_terms/fixation_type/suggestions",
        params={"term": "custom", "include_other_ontology_values": True},
    )
    assert resp.status_code == 200
    results = [FieldValue.model_validate(r) for r in resp.json()]
    assert field_value("Custom fix", 1) in results
