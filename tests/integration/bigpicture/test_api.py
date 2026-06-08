"""Integration tests for the API."""

# TODO(improve): use Snowstorm service
# TODO(improve): check that SNOMED CT codes assigned to the fields are correct

from typing import Any

import pytest
from fastapi.testclient import TestClient

from search_api.api.beacon.models import (
    BeaconQuery,
    BeaconQueryFilter,
    BeaconQueryRequest,
    BeaconResultSetsResponse,
)
from search_api.api.bigpicture.routes import get_beacon_service, get_snomed_service
from search_api.api.bigpicture.services.beacon import OpenSearchBigpictureBeaconService
from search_api.main import app
from search_api.services.snomed import SnomedService

DATASET_1 = "dataset_1"
DATASET_2 = "dataset_2"

HUMAN = "337915000"
MOUSE = "447612001"

BREAST = "80248007"
PELVIS = "41216001"
KIDNEY = "64033007"

FFPE = "431510009"
FROZEN_FIX = "1286895009"

PARAFFIN = "311731000"
FROZEN_PREP = "433469005"

HE = "12710003"
IHC = "406917005"
ISH = "115959002"

SPECIMEN_TYPE = "119376003"

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
                "species": HUMAN,
                "sex": "Female",
                "anatomical_site": [BREAST],
                "fixation_type": FFPE,
                "block_preparation": PARAFFIN,
                "specimen_type": SPECIMEN_TYPE,
                "age_at_extraction": {"gte": 16425, "lte": 18250},
            }
        ],
        "stains": [
            {
                "staining_procedure": HE,
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
                "species": HUMAN,
                "sex": "Female",
                "anatomical_site": [BREAST],
                "fixation_type": FFPE,
                "block_preparation": PARAFFIN,
                "specimen_type": SPECIMEN_TYPE,
                "age_at_extraction": {"gte": 20075, "lte": 21900},
            }
        ],
        "stains": [
            {
                "staining_procedure": IHC,
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
                "species": HUMAN,
                "sex": "Male",
                "anatomical_site": [PELVIS, KIDNEY],
                "fixation_type": FFPE,
                "block_preparation": PARAFFIN,
                "specimen_type": SPECIMEN_TYPE,
                "age_at_extraction": {"gte": 23725, "lte": 25550},
            }
        ],
        "stains": [
            {
                "staining_procedure": HE,
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
                "species": MOUSE,
                "sex": "Male",
                "anatomical_site": [KIDNEY],
                "fixation_type": FROZEN_FIX,
                "block_preparation": FROZEN_PREP,
                "specimen_type": SPECIMEN_TYPE,
                "age_at_extraction": {"gte": 0, "lte": 365},
            }
        ],
        "stains": [
            {
                "staining_procedure": HE,
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
                "species": MOUSE,
                "sex": "Female",
                "anatomical_site": [KIDNEY],
                "fixation_type": FFPE,
                "block_preparation": PARAFFIN,
                "specimen_type": SPECIMEN_TYPE,
                "age_at_extraction": {"gte": 0, "lte": 365},
            }
        ],
        "stains": [
            {
                "staining_procedure": ISH,
                "staining_procedure_text": "In situ hybridization",
                "staining_substance": "Double-stranded DNA",
                "staining_substance_text": "Double-stranded DNA",
            }
        ],
    },
]


class MockSnomedService(SnomedService):
    """SnomedService that skips SNOMED expansion."""

    async def prepare_ontology_filter(self, f, filtering_terms, branch="MAIN"):
        return f


@pytest.fixture(scope="module")
def bp_opensearch_docs() -> list[dict[str, Any]]:
    return OPENSEARCH_DOCS


@pytest.fixture(scope="module", autouse=True)
def _override_dependencies(bp_opensearch_index_name: str):
    """Point get_beacon_service at the UUID test index and swap out SNOMED."""

    def _beacon_service() -> OpenSearchBigpictureBeaconService:
        from search_api.api.opensearch.services.search import create_search

        return OpenSearchBigpictureBeaconService(
            create_search(), bp_opensearch_index_name
        )

    saved = dict(app.dependency_overrides)
    app.dependency_overrides.clear()
    app.dependency_overrides[get_beacon_service] = _beacon_service
    app.dependency_overrides[get_snomed_service] = lambda: MockSnomedService()
    yield
    app.dependency_overrides.clear()
    app.dependency_overrides.update(saved)


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(app)


def get_filters(
    *field_id_value_pairs: tuple[str, str | list[str]],
) -> list[BeaconQueryFilter]:
    return [
        BeaconQueryFilter(id=field_id, value=value, includeDescendantTerms=False)
        for field_id, value in field_id_value_pairs
    ]


def query(
    client: TestClient, *field_id_value_pairs: tuple[str, str | list[str]]
) -> BeaconResultSetsResponse:
    request = BeaconQueryRequest(
        query=BeaconQuery(
            filters=get_filters(*field_id_value_pairs),
            requestedGranularity="record",
        )
    )
    resp = client.post("/query", json=request.model_dump())
    assert resp.status_code == 200
    return BeaconResultSetsResponse.model_validate(resp.json())


def get_dataset_ids(response: BeaconResultSetsResponse) -> set[str]:
    return {rs.id for rs in response.response.resultSet}


def get_matching_image_count(
    response: BeaconResultSetsResponse, dataset_id: str
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
    result = query(client, ("animal_species", HUMAN))
    assert get_dataset_ids(result) == {DATASET_1}
    assert get_matching_image_count(result, DATASET_1) == 3


@pytest.mark.asyncio
async def test_query_filter_mouse_species(bp_opensearch_index, client):
    result = query(client, ("animal_species", MOUSE))
    assert get_dataset_ids(result) == {DATASET_2}
    assert get_matching_image_count(result, DATASET_2) == 2


@pytest.mark.asyncio
async def test_query_filter_both_species(bp_opensearch_index, client):
    result = query(client, ("animal_species", [HUMAN, MOUSE]))
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
    result = query(client, ("anatomical_site", BREAST))
    assert get_dataset_ids(result) == {DATASET_1}
    assert get_matching_image_count(result, DATASET_1) == 2  # image_1, image_2


@pytest.mark.asyncio
async def test_query_filter_anatomical_site_kidney(bp_opensearch_index, client):
    result = query(client, ("anatomical_site", KIDNEY))
    assert get_dataset_ids(result) == {DATASET_1, DATASET_2}
    assert get_matching_image_count(result, DATASET_1) == 1  # image_3
    assert get_matching_image_count(result, DATASET_2) == 2


# Fixation type
#


@pytest.mark.asyncio
async def test_query_filter_fixation_ffpe(bp_opensearch_index, client):
    result = query(client, ("fixation_type", FFPE))
    assert get_dataset_ids(result) == {DATASET_1, DATASET_2}
    assert get_matching_image_count(result, DATASET_1) == 3
    assert get_matching_image_count(result, DATASET_2) == 1  # image_5


@pytest.mark.asyncio
async def test_query_filter_fixation_frozen(bp_opensearch_index, client):
    result = query(client, ("fixation_type", FROZEN_FIX))
    assert get_dataset_ids(result) == {DATASET_2}
    assert get_matching_image_count(result, DATASET_2) == 1  # image_4


# Block preparation
#


@pytest.mark.asyncio
async def test_query_filter_block_preparation_paraffin(bp_opensearch_index, client):
    result = query(client, ("block_preparation", PARAFFIN))
    assert get_dataset_ids(result) == {DATASET_1, DATASET_2}
    assert get_matching_image_count(result, DATASET_1) == 3
    assert get_matching_image_count(result, DATASET_2) == 1  # image_5


@pytest.mark.asyncio
async def test_query_filter_block_preparation_frozen(bp_opensearch_index, client):
    result = query(client, ("block_preparation", FROZEN_PREP))
    assert get_dataset_ids(result) == {DATASET_2}
    assert get_matching_image_count(result, DATASET_2) == 1  # image_4


# Staining procedure
#


@pytest.mark.asyncio
async def test_query_filter_staining_he(bp_opensearch_index, client):
    result = query(client, ("staining_procedure", HE))
    assert get_dataset_ids(result) == {DATASET_1, DATASET_2}
    assert get_matching_image_count(result, DATASET_1) == 2  # image_1, image_3
    assert get_matching_image_count(result, DATASET_2) == 1  # image_4


@pytest.mark.asyncio
async def test_query_filter_staining_ihc(bp_opensearch_index, client):
    result = query(client, ("staining_procedure", IHC))
    assert get_dataset_ids(result) == {DATASET_1}
    assert get_matching_image_count(result, DATASET_1) == 1  # image_2


@pytest.mark.asyncio
async def test_query_filter_staining_ish(bp_opensearch_index, client):
    result = query(client, ("staining_procedure", ISH))
    assert get_dataset_ids(result) == {DATASET_2}
    assert get_matching_image_count(result, DATASET_2) == 1  # image_5


@pytest.mark.asyncio
async def test_query_filter_staining_he_and_ihc(bp_opensearch_index, client):
    result = query(client, ("staining_procedure", [HE, IHC]))
    assert get_dataset_ids(result) == {DATASET_1, DATASET_2}
    assert get_matching_image_count(result, DATASET_1) == 3  # all three match HE or IHC
    assert get_matching_image_count(result, DATASET_2) == 1  # image_4 (HE)


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
    result = query(client, ("animal_species", HUMAN), ("staining_procedure", HE))
    assert get_dataset_ids(result) == {DATASET_1}
    assert get_matching_image_count(result, DATASET_1) == 2  # image_1, image_3


@pytest.mark.asyncio
async def test_query_combined_species_and_anatomical_site(bp_opensearch_index, client):
    result = query(client, ("animal_species", MOUSE), ("anatomical_site", KIDNEY))
    assert get_dataset_ids(result) == {DATASET_2}
    assert get_matching_image_count(result, DATASET_2) == 2


@pytest.mark.asyncio
async def test_query_combined_no_match(bp_opensearch_index, client):
    # Human species + ISH staining (only in mouse dataset) → no results.
    result = query(client, ("animal_species", HUMAN), ("staining_procedure", ISH))
    assert get_dataset_ids(result) == set()


# Response granularity
#


@pytest.mark.asyncio
async def test_query_boolean_granularity(bp_opensearch_index, client):
    request = BeaconQueryRequest(
        query=BeaconQuery(
            filters=get_filters(("animal_species", HUMAN)),
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
            filters=get_filters(("animal_species", HUMAN)),
            requestedGranularity="count",
        )
    )
    resp = client.post("/query", json=request.model_dump())
    assert resp.status_code == 200
    data = resp.json()
    assert data["responseSummary"]["exists"] is True
    assert data["responseSummary"]["numTotalResults"] == 1  # 1 matching dataset
