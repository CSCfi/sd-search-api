import json
from typing import override

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from search_api.api.beacon.models import (
    SNOMED_ONTOLOGY_ID,
    BeaconQuery,
    BeaconQueryRequest,
    BeaconBooleanResponse,
    BeaconCountResponse,
    BeaconResultSet,
    BeaconResultSets,
)
from search_api.api.bigpicture.models import (
    BP_FILTERING_QUALIFIERS,
    BigpictureBeaconResultSetResult,
    BigpictureBeaconResultSetsResponse,
)
from search_api.api.beacon.services import BeaconService
from search_api.api.opensearch.models import (
    OpenSearchBeaconFilteringTerm,
    OpenSearchOntologyOrValue,
)
from search_api.api.bigpicture.models import (
    BP_FILTERING_GROUPS,
    BP_FILTERING_SCOPES,
    BP_FILTERING_TERMS,
    BP_INFO_RESPONSE,
    BP_FILTERING_TERMS_RESPONSE,
)
from search_api.api.bigpicture.domain import BP_DOMAIN
from search_api.api.beacon.routes import (
    get_beacon_service,
    get_ontology_term_services,
    make_beacon_router,
)
from search_api.api.exception_handlers import register_exception_handlers
from search_api.api.models import FieldValue, IndexedFieldValueCounts
from search_api.services.ontology.term_cache import OntologyTermCacheService

app = FastAPI()
app.include_router(make_beacon_router(BP_DOMAIN))
register_exception_handlers(app)


def get_mock_query_result() -> BeaconResultSets[BigpictureBeaconResultSetResult]:
    results: BeaconResultSets[BigpictureBeaconResultSetResult] = BeaconResultSets()
    results.resultSet.append(
        BeaconResultSet[BigpictureBeaconResultSetResult](
            id="testDataset",
            results=[
                BigpictureBeaconResultSetResult(
                    datasetId="testDataset",
                    datasetTitle="testTitle",
                    datasetDescription="testDescription",
                    datasetUrl="https://datasets.bigipicture.eu/datasets/testDataset.html",
                    totalImageCount=1,
                    matchingImageCount=1,
                    imageIds=["testImage"],
                )
            ],
        )
    )
    return results


class MockBeaconService(
    BeaconService[OpenSearchBeaconFilteringTerm, BigpictureBeaconResultSetResult]
):
    @override
    async def query(
        self, filters, granularity="record", scope=None, qualifiers=None
    ) -> BeaconResultSets[BigpictureBeaconResultSetResult]:
        return get_mock_query_result()

    @override
    async def is_healthy(self) -> bool:
        return True

    @override
    async def get_indexed_field_value_counts(
        self, field_id: str, scope=None, qualifiers=None
    ) -> IndexedFieldValueCounts:
        term = self.get_term(field_id)
        if isinstance(term.opensearch_field, OpenSearchOntologyOrValue):
            return IndexedFieldValueCounts(counts={}, other_counts={})
        return IndexedFieldValueCounts(counts={})


SUGGESTIONS_AND_VALUES_INDEXED_COUNTS: dict[str, IndexedFieldValueCounts] = {
    "sex": IndexedFieldValueCounts(counts={"Male": 10, "Female": 8}),
    "animal_species": IndexedFieldValueCounts(counts={"410607006": 5, "388480002": 3}),
    "fixation_type": IndexedFieldValueCounts(
        counts={"1388477003": 4}, other_counts={"Formalin": 2, "Custom fix": 1}
    ),
}

PREFERRED_TERMS: dict[str, str] = {
    "410607006": "Homo sapiens",
    "388480002": "Sus scrofa",
    "1388477003": "Tissue fixative",
}


class MockOntologyTermCacheService(OntologyTermCacheService):
    @override
    async def load(self) -> None:
        pass

    @override
    async def get_preferred_terms(
        self, field_id: str, concept_ids: set[str]
    ) -> dict[str, str]:
        return {
            cid: PREFERRED_TERMS[cid] for cid in concept_ids if cid in PREFERRED_TERMS
        }

    @override
    async def cache_preferred_terms(self, field_id, concept_ids, snomed) -> None:
        pass

    @override
    async def get_concept_ids_by_term(self, field_id: str, term: str) -> set[str]:
        return {cid for cid, t in PREFERRED_TERMS.items() if t == term}

    @override
    async def refresh(self, snomed) -> None:
        pass


class MockSuggestionsAndValuesBeaconService(MockBeaconService):
    @override
    async def get_indexed_field_value_counts(
        self, field_id: str, scope=None, qualifiers=None
    ) -> IndexedFieldValueCounts:
        if field_id in SUGGESTIONS_AND_VALUES_INDEXED_COUNTS:
            return SUGGESTIONS_AND_VALUES_INDEXED_COUNTS[field_id]
        raise ValueError(f"Unsupported field: '{field_id}'")


@pytest.fixture()
def client():
    saved = dict(app.dependency_overrides)
    app.dependency_overrides[get_beacon_service] = lambda: MockBeaconService(
        BP_FILTERING_TERMS
    )
    app.dependency_overrides[get_ontology_term_services] = lambda: {
        SNOMED_ONTOLOGY_ID: MockOntologyTermCacheService()
    }
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
    response = BigpictureBeaconResultSetsResponse.model_validate(resp.json())
    assert response.responseSummary.exists
    assert response.responseSummary.numTotalResults == 1
    assert response.response.resultSet[0].results[0].matchingImageCount == 1
    assert response.response.resultSet[0].results[0].datasetUrl == (
        "https://datasets.bigipicture.eu/datasets/testDataset.html"
    )


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


def test_filtering_groups(client: TestClient):
    response = client.get("/filtering_groups")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) == len(BP_FILTERING_GROUPS)
    ids = [g["id"] for g in data]
    assert ids == [g.id for g in BP_FILTERING_GROUPS]
    for group in data:
        assert "id" in group
        assert "label" in group


def test_filtering_scopes(client: TestClient):
    response = client.get("/filtering_scopes")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) == len(BP_FILTERING_SCOPES)
    ids = [s["id"] for s in data]
    assert ids == [s.id for s in BP_FILTERING_SCOPES]
    for scope in data:
        assert "id" in scope
        assert "label" in scope


@pytest.fixture()
def suggestions_values_client():
    saved = dict(app.dependency_overrides)
    app.dependency_overrides[get_beacon_service] = lambda: (
        MockSuggestionsAndValuesBeaconService(BP_FILTERING_TERMS)
    )
    app.dependency_overrides[get_ontology_term_services] = lambda: {
        SNOMED_ONTOLOGY_ID: MockOntologyTermCacheService()
    }
    yield TestClient(app)
    app.dependency_overrides.clear()
    app.dependency_overrides.update(saved)


# Filtering term suggestions
#


def test_filtering_term_suggestions_unknown_field(suggestions_values_client):
    resp = suggestions_values_client.get(
        "/filtering_terms/unknown/suggestions", params={"term": "x"}
    )
    assert resp.status_code == 400
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
    assert [FieldValue.model_validate(r) for r in resp.json()] == [
        FieldValue(value="Male", count=10)
    ]
    resp = suggestions_values_client.get(
        "/filtering_terms/sex/suggestions",
        params={"term": "FE", "include_all_controlled_values": True},
    )
    assert resp.status_code == 200
    assert [FieldValue.model_validate(r) for r in resp.json()] == [
        FieldValue(value="Female", count=8)
    ]


def test_filtering_term_suggestions_controlled_value_indexed_only(
    suggestions_values_client,
):
    resp = suggestions_values_client.get(
        "/filtering_terms/sex/suggestions",
        params={"term": "o", "include_all_controlled_values": True},
    )
    assert resp.status_code == 200
    assert [FieldValue.model_validate(r) for r in resp.json()] == [
        FieldValue(value="Other", count=0)  # "Other" is not indexed
    ]
    resp = suggestions_values_client.get(
        "/filtering_terms/sex/suggestions",
        params={"term": "o", "include_all_controlled_values": False},
    )
    assert resp.status_code == 200
    assert [FieldValue.model_validate(r) for r in resp.json()] == []


def test_filtering_term_suggestions_controlled_value_substring_match(
    suggestions_values_client,
):
    resp = suggestions_values_client.get(
        "/filtering_terms/sex/suggestions",
        params={"term": "ale", "substring_match": False},
    )
    assert resp.status_code == 200
    assert [FieldValue.model_validate(r) for r in resp.json()] == []
    resp = suggestions_values_client.get(
        "/filtering_terms/sex/suggestions",
        params={"term": "ale", "substring_match": True},
    )
    assert resp.status_code == 200
    assert [FieldValue.model_validate(r) for r in resp.json()] == [
        FieldValue(value="Female", count=8),
        FieldValue(value="Male", count=10),
    ]


def test_filtering_term_suggestions_ontology_include_other(suggestions_values_client):
    resp = suggestions_values_client.get(
        "/filtering_terms/fixation_type/suggestions",
        params={"term": "fo", "include_other_ontology_values": True},
    )
    assert resp.status_code == 200
    results = [FieldValue.model_validate(r) for r in resp.json()]
    assert FieldValue(value="Formalin", count=2) in results

    resp = suggestions_values_client.get(
        "/filtering_terms/fixation_type/suggestions",
        params={"term": "fo", "include_other_ontology_values": False},
    )
    assert resp.status_code == 200
    results = [FieldValue.model_validate(r) for r in resp.json()]
    assert not any(r.value == "Formalin" for r in results)


# Filtering term values
#


def test_filtering_term_values_unknown_field(suggestions_values_client):
    resp = suggestions_values_client.get("/filtering_terms/unknown/values")
    assert resp.status_code == 400
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
    results = [FieldValue.model_validate(r) for r in resp.json()]
    assert {r.value: r.count for r in results} == {"Male": 10, "Female": 8}

    resp = suggestions_values_client.get(
        "/filtering_terms/sex/values",
        params={"include_all_controlled_values": True},
    )
    assert resp.status_code == 200
    results = [FieldValue.model_validate(r) for r in resp.json()]
    assert {r.value: r.count for r in results} == {
        "Male": 10,
        "Female": 8,
        "Not-known": 0,
        "Other": 0,
    }


def test_filtering_term_values_ontology_indexed(suggestions_values_client):
    resp = suggestions_values_client.get("/filtering_terms/animal_species/values")
    assert resp.status_code == 200
    results = [FieldValue.model_validate(r) for r in resp.json()]
    assert {r.value: r.count for r in results} == {
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
    results = [FieldValue.model_validate(r) for r in resp.json()]
    assert {r.value: r.count for r in results} == {
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
    results = [FieldValue.model_validate(r) for r in resp.json()]
    assert {r.value: r.count for r in results} == {"Tissue fixative": 4}


def test_filtering_term_values_sorted_by_count(suggestions_values_client):
    resp = suggestions_values_client.get("/filtering_terms/animal_species/values")
    assert resp.status_code == 200
    results = [FieldValue.model_validate(r) for r in resp.json()]
    counts = [r.count for r in results]
    assert counts == sorted(counts, reverse=True)


class OnlyHomoSapiensCacheService(MockOntologyTermCacheService):
    """Returns only Homo sapiens as valid for animal_species."""

    @override
    async def get_preferred_terms(
        self, field_id: str, concept_ids: set[str]
    ) -> dict[str, str]:
        if field_id == "animal_species":
            return {"410607006": "Homo sapiens"} if "410607006" in concept_ids else {}
        return await super().get_preferred_terms(field_id, concept_ids)


def test_filtering_term_values_excludes_unexpected():
    saved = dict(app.dependency_overrides)
    app.dependency_overrides[get_beacon_service] = lambda: (
        MockSuggestionsAndValuesBeaconService(BP_FILTERING_TERMS)
    )
    app.dependency_overrides[get_ontology_term_services] = lambda: {
        SNOMED_ONTOLOGY_ID: OnlyHomoSapiensCacheService()
    }
    try:
        resp = TestClient(app).get("/filtering_terms/animal_species/values")
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(saved)

    assert resp.status_code == 200
    results = [FieldValue.model_validate(r) for r in resp.json()]
    assert len(results) == 1
    assert results[0].value == "Homo sapiens"


def test_filtering_term_suggestions_excludes_unexpected():
    saved = dict(app.dependency_overrides)
    app.dependency_overrides[get_beacon_service] = lambda: (
        MockSuggestionsAndValuesBeaconService(BP_FILTERING_TERMS)
    )
    app.dependency_overrides[get_ontology_term_services] = lambda: {
        SNOMED_ONTOLOGY_ID: OnlyHomoSapiensCacheService()
    }
    try:
        resp = TestClient(app).get(
            "/filtering_terms/animal_species/suggestions", params={"term": "su"}
        )
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(saved)

    assert resp.status_code == 200
    assert resp.json() == []


def test_filtering_qualifiers(client: TestClient):
    response = client.get("/filtering_qualifiers")
    assert response.status_code == 200
    assert response.json() == [q.model_dump() for q in BP_FILTERING_QUALIFIERS]


def test_query_rejects_unknown_qualifier(client: TestClient):
    request = BeaconQueryRequest(
        query=BeaconQuery(requestedQualifiers={"certainty": ["confirmed"]})
    )
    response = client.post("/query", json=request.model_dump())
    assert response.status_code == 400
    assert "Unsupported qualifier" in response.text


def test_query_rejects_unknown_qualifier_value(client: TestClient):
    request = BeaconQueryRequest(
        query=BeaconQuery(requestedQualifiers={"observation": ["known"]})
    )
    response = client.post("/query", json=request.model_dump())
    assert response.status_code == 400
    assert "Unsupported value(s) ['known']" in response.text


def test_query_accepts_absent_qualifiers(client: TestClient):
    """An absent qualifier is not filtered on, so the query is valid without one."""
    request = BeaconQueryRequest(query=BeaconQuery(requestedGranularity="boolean"))
    assert request.query.requestedQualifiers == {}
    assert client.post("/query", json=request.model_dump()).status_code == 200


@pytest.mark.parametrize("path", ["values", "suggestions"])
def test_filtering_term_qualifier_param_is_validated(client: TestClient, path: str):
    params = {"qualifier": "observation:confirmed"}
    if path == "suggestions":
        params["term"] = "a"
    assert client.get(f"/filtering_terms/sex/{path}", params=params).status_code == 200

    # A malformed param names no value.
    bad = dict(params, qualifier="observation")
    response = client.get(f"/filtering_terms/sex/{path}", params=bad)
    assert response.status_code == 400
    assert "expected '<qualifier id>:<value>'" in response.text


@pytest.mark.parametrize("path", ["values", "suggestions"])
def test_filtering_term_scope_param_is_validated(client: TestClient, path: str):
    params = {"scope": "clinical"}
    if path == "suggestions":
        params["term"] = "a"
    assert client.get(f"/filtering_terms/sex/{path}", params=params).status_code == 200

    bad = dict(params, scope="preclinical")
    response = client.get(f"/filtering_terms/sex/{path}", params=bad)
    assert response.status_code == 400
    assert "Unsupported scope: 'preclinical'" in response.text


@pytest.mark.parametrize("path", ["values", "suggestions"])
def test_filtering_term_without_scope_or_qualifier_counts_everything(
    client: TestClient, path: str
):
    """Neither axis has a default, so omitting both restricts nothing."""
    params = {"term": "a"} if path == "suggestions" else {}
    assert client.get(f"/filtering_terms/sex/{path}", params=params).status_code == 200


@pytest.mark.parametrize("path", ["values", "suggestions"])
def test_filtering_term_rejects_a_scope_the_field_is_not_in(
    client: TestClient, path: str
):
    """diagnosis is clinical-only, so counting it as non-clinical is a client error."""
    params = {"scope": "non_clinical"}
    if path == "suggestions":
        params["term"] = "a"
    response = client.get(f"/filtering_terms/diagnosis/{path}", params=params)
    assert response.status_code == 400
    assert "Field 'diagnosis' is not in scope 'non_clinical'" in response.text

    # Its own scope is accepted.
    ok = dict(params, scope="clinical")
    assert (
        client.get(f"/filtering_terms/diagnosis/{path}", params=ok).status_code == 200
    )
