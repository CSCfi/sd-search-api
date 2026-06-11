import json
from typing import override

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from search_api.api.beacon.models import (
    BeaconQueryRequest,
    BeaconQuery,
    BeaconBooleanResponse,
    BeaconCountResponse,
    BeaconResultSet,
    BeaconResultSetResult,
    BeaconResultSets,
    BeaconResultSetsResponse,
)
from search_api.api.beacon.services import BeaconService
from search_api.api.opensearch.models import OpenSearchOntologyOrValue
from search_api.api.bigpicture.models import (
    BP_FILTERING_TERMS,
    BP_INFO_RESPONSE,
    BP_FILTERING_TERMS_RESPONSE,
)
from search_api.api.bigpicture.routes import (
    get_beacon_service,
    get_snomed_service,
    router,
)
from search_api.api.models import FieldValueSuggestion
from search_api.services.snomed import SnomedConcept, SnomedService

app = FastAPI()
app.include_router(router)


def get_mock_query_result() -> BeaconResultSets:
    results = BeaconResultSets()
    results.resultSet.append(
        BeaconResultSet(
            id="testDataset",
            results=[
                BeaconResultSetResult(
                    datasetId="testDataset",
                    datasetTitle="testTitle",
                    datasetDescription="testDescription",
                    totalImageCount=1,
                    matchingImageCount=1,
                    imageIds=["testImage"],
                )
            ],
        )
    )
    return results


class MockBeaconService(BeaconService):
    @override
    async def query(self, filters, granularity="record") -> BeaconResultSets:
        return get_mock_query_result()

    @override
    async def is_healthy(self) -> bool:
        return True

    @override
    async def get_indexed_field_value_counts(
        self, field_id: str
    ) -> list[dict[str, int]]:
        term = self.get_term(field_id)
        if isinstance(term.opensearch_field, OpenSearchOntologyOrValue):
            return [{}, {}]
        return [{}]


def _ecl(field_id: str) -> str:
    return next(t for t in BP_FILTERING_TERMS if t.id == field_id).snomed_ecl


# Concepts keyed by ecl expression.
SUGGESTIONS_AND_VALUES_CONCEPTS: dict[str, dict[str, SnomedConcept]] = {
    _ecl("animal_species"): {
        "410607006": SnomedConcept(
            concept_id="410607006", preferred_term="Homo sapiens"
        ),
        "388480002": SnomedConcept(concept_id="388480002", preferred_term="Sus scrofa"),
        "hominin_001": SnomedConcept(
            concept_id="hominin_001", preferred_term="Homo heidelbergensis"
        ),
    },
    _ecl("fixation_type"): {
        "1388477003": SnomedConcept(
            concept_id="1388477003", preferred_term="Tissue fixative"
        ),
    },
}

SUGGESTIONS_AND_VALUES_INDEXED_COUNTS: dict[str, list[dict[str, int]]] = {
    "sex": [{"Male": 10, "Female": 8}],
    "animal_species": [{"410607006": 5, "388480002": 3}],
    "fixation_type": [{"1388477003": 4}, {"Formalin": 2, "Custom fix": 1}],
}


class MockSuggestionsAndValuesBeaconService(MockBeaconService):
    @override
    async def get_indexed_field_value_counts(
        self, field_id: str
    ) -> list[dict[str, int]]:
        if field_id in SUGGESTIONS_AND_VALUES_INDEXED_COUNTS:
            return SUGGESTIONS_AND_VALUES_INDEXED_COUNTS[field_id]
        raise ValueError(f"Unsupported field: '{field_id}'")


class MockSuggestionsAndValuesSnomedService(SnomedService):
    @override
    async def suggest_concepts(
        self,
        term: str,
        ecl: str,
        branch: str = "MAIN",
        limit: int = 10,
        indexed_concept_ids: set[str] | None = None,
    ) -> list[SnomedConcept]:
        return [
            concept
            for concept in SUGGESTIONS_AND_VALUES_CONCEPTS.get(ecl, {}).values()
            if concept.preferred_term.lower().startswith(term.lower())
        ]

    @override
    async def get_concepts(
        self,
        concept_ids: set[str] | None,
        ecl: str,
        branch: str = "MAIN",
    ) -> dict[str, SnomedConcept]:
        return {
            k: v
            for k, v in SUGGESTIONS_AND_VALUES_CONCEPTS.get(ecl, {}).items()
            if concept_ids is None or k in concept_ids
        }


@pytest.fixture()
def client():
    saved = dict(app.dependency_overrides)
    app.dependency_overrides[get_beacon_service] = lambda: MockBeaconService(
        BP_FILTERING_TERMS
    )
    yield TestClient(app)
    app.dependency_overrides.clear()
    app.dependency_overrides.update(saved)


def test_query(client: TestClient):
    request = BeaconQueryRequest(query=BeaconQuery(requestedGranularity="boolean"))
    resp = client.post("/query", json=request.model_dump())
    assert resp.status_code == 200
    assert BeaconBooleanResponse.model_validate(resp.json()).responseSummary.exists

    request = BeaconQueryRequest(query=BeaconQuery(requestedGranularity="count"))
    resp = client.post("/query", json=request.model_dump())
    assert resp.status_code == 200
    response = BeaconCountResponse.model_validate(resp.json())
    assert response.responseSummary.exists
    assert response.responseSummary.numTotalResults == 1

    request = BeaconQueryRequest(query=BeaconQuery(requestedGranularity="record"))
    resp = client.post("/query", json=request.model_dump())
    assert resp.status_code == 200
    response = BeaconResultSetsResponse.model_validate(resp.json())
    assert response.responseSummary.exists
    assert response.responseSummary.numTotalResults == 1
    assert response.response.resultSet == get_mock_query_result().resultSet


def test_info(client: TestClient):
    response = client.get("/info")
    assert response.status_code == 200
    assert json.dumps(response.json()) == json.dumps(
        BP_INFO_RESPONSE.model_dump(exclude_none=True)
    )


def test_filtering_terms(client: TestClient):
    response = client.get("/filtering_terms")
    assert response.status_code == 200
    assert json.dumps(response.json()) == json.dumps(
        BP_FILTERING_TERMS_RESPONSE.model_dump(exclude_none=True)
    )


@pytest.fixture()
def suggestions_values_client():
    saved = dict(app.dependency_overrides)
    app.dependency_overrides[get_beacon_service] = lambda: (
        MockSuggestionsAndValuesBeaconService(BP_FILTERING_TERMS)
    )
    app.dependency_overrides[get_snomed_service] = MockSuggestionsAndValuesSnomedService
    yield TestClient(app)
    app.dependency_overrides.clear()
    app.dependency_overrides.update(saved)


# Filtering term suggestions
#


def test_filtering_term_suggestions_unknown_field(suggestions_values_client):
    resp = suggestions_values_client.get(
        "/filtering_terms/unknown/suggestions", params={"term": "x"}
    )
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Unknown field: 'unknown'."


def test_filtering_term_suggestions_unsupported_type(suggestions_values_client):
    resp = suggestions_values_client.get(
        "/filtering_terms/dataset_title/suggestions", params={"term": "x"}
    )
    assert resp.status_code == 400
    assert (
        resp.json()["detail"]
        == "Suggestions are not supported for field 'dataset_title' (type 'text')."
    )


def test_filtering_term_suggestions_controlled_value_all(suggestions_values_client):
    resp = suggestions_values_client.get(
        "/filtering_terms/sex/suggestions",
        params={"term": "ma", "include_all_controlled_values": True},
    )
    assert resp.status_code == 200
    assert [FieldValueSuggestion.model_validate(r).term for r in resp.json()] == [
        "Male"
    ]
    resp = suggestions_values_client.get(
        "/filtering_terms/sex/suggestions",
        params={"term": "FE", "include_all_controlled_values": True},
    )
    assert resp.status_code == 200
    assert [FieldValueSuggestion.model_validate(r).term for r in resp.json()] == [
        "Female"
    ]


def test_filtering_term_suggestions_controlled_value_indexed_only(
    suggestions_values_client,
):
    resp = suggestions_values_client.get(
        "/filtering_terms/sex/suggestions",
        params={"term": "o", "include_all_controlled_values": True},
    )
    assert resp.status_code == 200
    assert [FieldValueSuggestion.model_validate(r).term for r in resp.json()] == [
        "Other"
    ]
    resp = suggestions_values_client.get(
        "/filtering_terms/sex/suggestions",
        params={"term": "o", "include_all_controlled_values": False},
    )
    assert resp.status_code == 200
    assert [FieldValueSuggestion.model_validate(r).term for r in resp.json()] == []


def test_filtering_term_suggestions_controlled_value_substring_match(
    suggestions_values_client,
):
    resp = suggestions_values_client.get(
        "/filtering_terms/sex/suggestions",
        params={"term": "ale", "substring_match": False},
    )
    assert resp.status_code == 200
    assert [FieldValueSuggestion.model_validate(r).term for r in resp.json()] == []
    resp = suggestions_values_client.get(
        "/filtering_terms/sex/suggestions",
        params={"term": "ale", "substring_match": True},
    )
    assert resp.status_code == 200
    assert [FieldValueSuggestion.model_validate(r).term for r in resp.json()] == [
        "Female",
        "Male",
    ]


def test_filtering_term_suggestions_ontology_include_all(suggestions_values_client):
    resp = suggestions_values_client.get(
        "/filtering_terms/animal_species/suggestions",
        params={"term": "Homo", "include_all_ontology_values": True},
    )
    assert resp.status_code == 200
    assert [FieldValueSuggestion.model_validate(r).term for r in resp.json()] == [
        "Homo sapiens",
        "Homo heidelbergensis",
    ]
    resp = suggestions_values_client.get(
        "/filtering_terms/animal_species/suggestions",
        params={"term": "Homo", "include_all_ontology_values": False},
    )
    assert resp.status_code == 200
    assert [FieldValueSuggestion.model_validate(r).term for r in resp.json()] == [
        "Homo sapiens"
    ]


def test_filtering_term_suggestions_ontology_include_other(suggestions_values_client):
    resp = suggestions_values_client.get(
        "/filtering_terms/fixation_type/suggestions",
        params={"term": "fo", "include_other_ontology_values": True},
    )
    assert resp.status_code == 200
    assert "Formalin" in [r["term"] for r in resp.json()]

    resp = suggestions_values_client.get(
        "/filtering_terms/fixation_type/suggestions",
        params={"term": "fo", "include_other_ontology_values": False},
    )
    assert resp.status_code == 200
    assert "Formalin" not in [r["term"] for r in resp.json()]


# Filtering term values
#


def test_filtering_term_values_unknown_field(suggestions_values_client):
    resp = suggestions_values_client.get("/filtering_terms/unknown/values")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Unknown field: 'unknown'."


def test_filtering_term_values_unsupported_type(suggestions_values_client):
    resp = suggestions_values_client.get("/filtering_terms/dataset_title/values")
    assert resp.status_code == 400
    assert (
        resp.json()["detail"]
        == "Values are not supported for field 'dataset_title' (type 'text')."
    )


def test_filtering_term_values_controlled_include_all(suggestions_values_client):
    resp = suggestions_values_client.get(
        "/filtering_terms/sex/values",
        params={"include_all_controlled_values": False},
    )
    assert resp.status_code == 200
    assert {r["value"]: r["count"] for r in resp.json()} == {"Male": 10, "Female": 8}

    resp = suggestions_values_client.get(
        "/filtering_terms/sex/values",
        params={"include_all_controlled_values": True},
    )
    assert resp.status_code == 200
    assert {r["value"]: r["count"] for r in resp.json()} == {
        "Male": 10,
        "Female": 8,
        "Not-known": 0,
        "Other": 0,
    }


def test_filtering_term_values_ontology_include_all(suggestions_values_client):
    resp = suggestions_values_client.get(
        "/filtering_terms/animal_species/values",
        params={"include_all_ontology_values": True},
    )
    assert resp.status_code == 200
    assert {r["value"]: r["count"] for r in resp.json()} == {
        "Homo sapiens": 5,
        "Sus scrofa": 3,
        "Homo heidelbergensis": 0,  # in SNOMED hierarchy, not indexed
    }

    resp = suggestions_values_client.get(
        "/filtering_terms/animal_species/values",
        params={"include_all_ontology_values": False},
    )
    assert resp.status_code == 200
    assert {r["value"]: r["count"] for r in resp.json()} == {
        "Homo sapiens": 5,
        "Sus scrofa": 3,
    }


def test_filtering_term_values_ontology_include_other(suggestions_values_client):
    resp = suggestions_values_client.get(
        "/filtering_terms/fixation_type/values",
        params={
            "include_all_ontology_values": False,
            "include_other_ontology_values": True,
        },
    )
    assert resp.status_code == 200
    assert {r["value"]: r["count"] for r in resp.json()} == {
        "Tissue fixative": 4,
        "Formalin": 2,  # free-text, only visible with include_other=True
        "Custom fix": 1,  # free-text, only visible with include_other=True
    }

    resp = suggestions_values_client.get(
        "/filtering_terms/fixation_type/values",
        params={
            "include_all_ontology_values": False,
            "include_other_ontology_values": False,
        },
    )
    assert resp.status_code == 200
    assert {r["value"]: r["count"] for r in resp.json()} == {
        "Tissue fixative": 4,
    }


def test_filtering_term_values_sorted_by_count(suggestions_values_client):
    resp = suggestions_values_client.get("/filtering_terms/animal_species/values")
    assert resp.status_code == 200
    counts = [r["count"] for r in resp.json()]
    assert counts == sorted(counts, reverse=True)
