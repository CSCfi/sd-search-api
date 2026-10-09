import json
from unittest.mock import ANY, AsyncMock
from datetime import datetime, timezone
from typing import override

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from search_api.api.beacon.models import (
    SNOMED_ONTOLOGY_ID,
    BeaconQueryFilter,
    BeaconQuery,
    BeaconQueryRequest,
    BeaconBooleanResponse,
    BeaconCountResponse,
    BeaconResultSet,
    BeaconResultSets,
)
from search_api.api.bigpicture.models import (
    BigpictureBeaconDatasetResult,
    BigpictureBeaconDatasetResultSetsResponse,
    BigpictureBeaconImageResult,
    BigpictureBeaconImageResultSetsResponse,
)
from search_api.api.beacon.services import (
    BeaconQueryResult,
    BeaconQueryService,
    BeaconService,
)
from search_api.api.opensearch.models import (
    OpenSearchBeaconFilteringTerm,
    OpenSearchOntologyOrValue,
)
from search_api.api.bigpicture.models import (
    BP_FILTERING_SCOPES,
    BP_FILTERING_TERM_BY_ID,
    BP_FILTERING_TERMS,
    BP_INFO_RESPONSE,
    BP_FILTERING_TERMS_RESPONSE,
)
from search_api.api.bigpicture.domain import BP_DOMAIN
from search_api.api.beacon import routes
from search_api.api.beacon.routes import (
    get_beacon_service,
    get_beacon_query_services,
    get_ontology_term_services,
    make_beacon_router,
)
from search_api.api.exception_handlers import register_exception_handlers
from search_api.api.models import FieldValue, ValueCounts

app = FastAPI()
app.include_router(make_beacon_router(BP_DOMAIN))
register_exception_handlers(app)


def get_mock_dataset_query_result() -> BeaconQueryResult[BigpictureBeaconDatasetResult]:
    results: BeaconResultSets[BigpictureBeaconDatasetResult] = BeaconResultSets()
    results.resultSet.append(
        BeaconResultSet[BigpictureBeaconDatasetResult](
            id="testDataset",
            results=[
                BigpictureBeaconDatasetResult(
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
    return BeaconQueryResult(total=len(results.resultSet), result_sets=results)


def get_mock_image_query_result() -> BeaconQueryResult[BigpictureBeaconImageResult]:
    results: BeaconResultSets[BigpictureBeaconImageResult] = BeaconResultSets()
    results.resultSet.append(
        BeaconResultSet[BigpictureBeaconImageResult](
            id="testImage",
            setType="image",
            results=[BigpictureBeaconImageResult(imageId="testImage")],
        )
    )
    return BeaconQueryResult(total=len(results.resultSet), result_sets=results)


# Indexed document counts, keyed as None for every document and value per scope.
_MOCK_INDEXED_COUNTS: dict[str | None, int] = {
    None: 18,
    "clinical": 10,
    "non_clinical": 8,
}


class MockBeaconService(BeaconService[OpenSearchBeaconFilteringTerm]):
    """The generic beacon service."""

    @override
    async def count_indexed(self, scope: str | None = None) -> int:
        return _MOCK_INDEXED_COUNTS[scope]

    @override
    async def is_healthy(self) -> bool:
        return True

    @override
    async def get_value_counts(self, field_id: str, scope=None) -> ValueCounts:
        term = self.get_term(field_id)
        if isinstance(term.opensearch_field, OpenSearchOntologyOrValue):
            return ValueCounts(counts={}, other_counts={})
        return ValueCounts(counts={})


class MockBeaconDatasetService(BeaconQueryService[BigpictureBeaconDatasetResult]):
    @override
    async def query(
        self, filters, granularity="record", scope=None
    ) -> BeaconQueryResult[BigpictureBeaconDatasetResult]:
        return get_mock_dataset_query_result()


class MockBeaconImageService(BeaconQueryService[BigpictureBeaconImageResult]):
    """Records what it was asked, so a test can check it."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    @override
    async def query(
        self, filters, granularity="record", scope=None
    ) -> BeaconQueryResult[BigpictureBeaconImageResult]:
        self.calls.append(dict(filters=filters, granularity=granularity, scope=scope))
        return get_mock_image_query_result()


PREFERRED_TERMS: dict[str, str] = {
    "410607006": "Homo sapiens",
    "78678003": "Sus scrofa",
    "1388477003": "Tissue fixative",
}


class MockOntologyTermCache:
    async def load(self) -> None:
        pass

    async def get_terms_by_concept_id(
        self, field_id: str, concept_ids: set[str]
    ) -> dict[str, str]:
        return {
            cid: PREFERRED_TERMS[cid] for cid in concept_ids if cid in PREFERRED_TERMS
        }

    async def cache_preferred_terms(self, field_id, concept_ids, snomed) -> None:
        pass

    async def get_concept_ids_by_term(self, field_id: str, term: str) -> set[str]:
        return {cid for cid, t in PREFERRED_TERMS.items() if t == term}

    async def refresh(self, snomed) -> None:
        pass


@pytest.fixture()
def image_service() -> MockBeaconImageService:
    return MockBeaconImageService()


@pytest.fixture()
def client(image_service):
    """Creates the test client and sets up mock services."""
    saved = dict(app.dependency_overrides)
    app.dependency_overrides[get_beacon_service] = lambda: MockBeaconService(
        BP_FILTERING_TERMS
    )
    app.dependency_overrides[get_beacon_query_services] = lambda: {
        "/datasets": MockBeaconDatasetService(),
        "/images": image_service,
    }
    app.dependency_overrides[get_ontology_term_services] = lambda: {
        SNOMED_ONTOLOGY_ID: MockOntologyTermCache()
    }
    yield TestClient(app)
    app.dependency_overrides.clear()
    app.dependency_overrides.update(saved)


def test_datasets_query(client: TestClient):
    request = BeaconQueryRequest(query=BeaconQuery(requestedGranularity="boolean"))
    resp = client.post("/datasets", json=request.model_dump())
    assert resp.status_code == 200
    assert BeaconBooleanResponse.model_validate(resp.json()).responseSummary.exists

    request = BeaconQueryRequest(query=BeaconQuery(requestedGranularity="count"))
    resp = client.post("/datasets", json=request.model_dump())
    assert resp.status_code == 200
    response = BeaconCountResponse.model_validate(resp.json())
    assert response.responseSummary.exists
    assert response.responseSummary.numTotalResults == 1

    request = BeaconQueryRequest(query=BeaconQuery(requestedGranularity="record"))
    resp = client.post("/datasets", json=request.model_dump())
    assert resp.status_code == 200
    response = BigpictureBeaconDatasetResultSetsResponse.model_validate(resp.json())
    assert response.responseSummary.exists
    assert response.responseSummary.numTotalResults == 1
    assert response.response.resultSet[0].results[0].matchingImageCount == 1
    assert response.response.resultSet[0].results[0].datasetUrl == (
        "https://datasets.bigipicture.eu/datasets/testDataset.html"
    )


def test_images_query(client: TestClient):
    request = BeaconQueryRequest(query=BeaconQuery(requestedGranularity="boolean"))
    resp = client.post("/images", json=request.model_dump())
    assert resp.status_code == 200
    assert BeaconBooleanResponse.model_validate(resp.json()).responseSummary.exists

    request = BeaconQueryRequest(query=BeaconQuery(requestedGranularity="count"))
    resp = client.post("/images", json=request.model_dump())
    assert resp.status_code == 200
    response = BeaconCountResponse.model_validate(resp.json())
    assert response.responseSummary.exists
    assert response.responseSummary.numTotalResults == 1

    request = BeaconQueryRequest(query=BeaconQuery(requestedGranularity="record"))
    resp = client.post("/images", json=request.model_dump())
    assert resp.status_code == 200
    response = BigpictureBeaconImageResultSetsResponse.model_validate(resp.json())
    assert response.responseSummary.exists
    assert response.responseSummary.numTotalResults == 1
    assert response.response.resultSet[0].results[0].imageId == "testImage"


def test_query_returns_count_by_default(client: TestClient):
    resp = client.post(
        "/images", json=BeaconQueryRequest(query=BeaconQuery()).model_dump()
    )
    assert resp.status_code == 200
    response = BeaconCountResponse.model_validate(resp.json())
    assert response.meta.returnedGranularity == "count"
    assert response.responseSummary.numTotalResults == 1


def test_query_passes_filters_and_scope(client: TestClient, image_service):
    scope = BP_FILTERING_SCOPES[0].id
    filters = [BeaconQueryFilter(id="sex", value="Female")]
    request = BeaconQueryRequest(
        query=BeaconQuery(filters=filters, requestedScope=scope)
    )
    resp = client.post("/images", json=request.model_dump())
    assert resp.status_code == 200
    [call] = image_service.calls
    assert call["filters"] == filters
    assert call["scope"] == scope


def test_query_rejects_invalid_scope(client: TestClient, image_service):
    request = BeaconQueryRequest(query=BeaconQuery(requestedScope="invalid"))
    resp = client.post("/images", json=request.model_dump())
    assert resp.status_code == 400
    assert image_service.calls == []


MOCK_CONCEPT_ID = "337915000"


class MockOntologyService:
    """Resolves every value to MOCK_CONCEPT_ID, or fails."""

    def __init__(self, fail: bool = False) -> None:
        self.fail = fail

    async def prepare_ontology_filter(self, query_filter, filtering_terms, term_cache):
        if self.fail:
            raise RuntimeError("Snowstorm is down.")
        return query_filter.model_copy(update={"value": [MOCK_CONCEPT_ID]})


def test_query_resolves_ontology_filters(
    client: TestClient, image_service, monkeypatch
):
    monkeypatch.setattr(routes, "get_ontology_service", lambda _: MockOntologyService())
    filters = [
        BeaconQueryFilter(id="animal_species", value="human"),
        BeaconQueryFilter(id="sex", value="Female"),
    ]
    request = BeaconQueryRequest(query=BeaconQuery(filters=filters))
    resp = client.post("/images", json=request.model_dump())
    assert resp.status_code == 200
    [call] = image_service.calls
    # Other filters first, then the resolved ontology filters.
    assert call["filters"] == [
        BeaconQueryFilter(id="sex", value="Female"),
        BeaconQueryFilter(id="animal_species", value=[MOCK_CONCEPT_ID]),
    ]


def test_query_returns_service_unavailable_when_ontology_fails(
    client: TestClient, image_service, monkeypatch
):
    monkeypatch.setattr(
        routes, "get_ontology_service", lambda _: MockOntologyService(fail=True)
    )
    filters = [BeaconQueryFilter(id="animal_species", value="x")]
    request = BeaconQueryRequest(query=BeaconQuery(filters=filters))
    resp = client.post("/images", json=request.model_dump())
    assert resp.status_code == 503
    assert image_service.calls == []


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


# Filtering term suggestions
#


def test_filtering_term_suggestions_unknown_field(client):
    resp = client.get("/filtering_terms/unknown/suggestions", params={"term": "x"})
    assert resp.status_code == 400
    assert resp.json()["detail"] == "Unknown field: 'unknown'."


def test_filtering_term_suggestions_unsupported_type(client):
    resp = client.get(
        "/filtering_terms/dataset_title/suggestions", params={"term": "x"}
    )
    assert resp.status_code == 400
    assert (
        resp.json()["detail"]
        == "Suggestions are not supported for field 'dataset_title' (type 'text')."
    )


def test_filtering_term_suggestions_calls_get_field_suggestions(
    client: TestClient, monkeypatch
):
    field_id = "sex"
    term = "fe"
    scope = "clinical"
    # Each option differs from its default to find any dropped parameters.
    substring_match = True
    include_all_controlled_values = True
    include_other_ontology_values = False
    mocked_field_values = [FieldValue(value="Female", count=8)]

    get_field_suggestions = AsyncMock(return_value=mocked_field_values)
    monkeypatch.setattr(routes, "get_field_suggestions", get_field_suggestions)
    resp = client.get(
        f"/filtering_terms/{field_id}/suggestions",
        params={
            "term": term,
            "scope": scope,
            "substring_match": substring_match,
            "include_all_controlled_values": include_all_controlled_values,
            "include_other_ontology_values": include_other_ontology_values,
        },
    )

    assert resp.status_code == 200
    assert [FieldValue.model_validate(v) for v in resp.json()] == mocked_field_values
    get_field_suggestions.assert_awaited_once_with(
        BP_FILTERING_TERM_BY_ID[field_id],
        term,
        scope,
        ANY,  # the beacon service
        ANY,  # the term caches
        BP_DOMAIN.ontology_id_by_field,
        substring_match=substring_match,
        include_all_controlled_values=include_all_controlled_values,
        include_other_ontology_values=include_other_ontology_values,
    )


# Filtering term values
#


def test_filtering_term_values_unknown_field(client):
    resp = client.get("/filtering_terms/unknown/values")
    assert resp.status_code == 400
    assert resp.json()["detail"] == "Unknown field: 'unknown'."


def test_filtering_term_values_unsupported_type(client):
    resp = client.get("/filtering_terms/dataset_title/values")
    assert resp.status_code == 400
    assert (
        resp.json()["detail"]
        == "Values are not supported for field 'dataset_title' (type 'text')."
    )


def test_filtering_term_values_calls_get_field_values(client: TestClient, monkeypatch):
    field_id = "sex"
    scope = "clinical"
    # Each option differs from its default to find any dropped parameters.
    include_all_controlled_values = True
    include_other_ontology_values = False
    mocked_field_values = [FieldValue(value="Female", count=8)]

    get_field_values = AsyncMock(return_value=mocked_field_values)
    monkeypatch.setattr(routes, "get_field_values", get_field_values)
    resp = client.get(
        f"/filtering_terms/{field_id}/values",
        params={
            "scope": scope,
            "include_all_controlled_values": include_all_controlled_values,
            "include_other_ontology_values": include_other_ontology_values,
        },
    )

    assert resp.status_code == 200
    assert [FieldValue.model_validate(v) for v in resp.json()] == mocked_field_values
    get_field_values.assert_awaited_once_with(
        BP_FILTERING_TERM_BY_ID[field_id],
        scope,
        ANY,  # the beacon service
        ANY,  # the term caches
        BP_DOMAIN.ontology_id_by_field,
        include_all_controlled_values=include_all_controlled_values,
        include_other_ontology_values=include_other_ontology_values,
    )


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
def test_filtering_term_without_scope_counts_everything(client: TestClient, path: str):
    """Scope has no default, so omitting it restricts nothing."""
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


_MOCK_LAST_INDEXED = datetime(2026, 8, 12, 9, 15, 22, tzinfo=timezone.utc)


def _mock_status_database_calls(monkeypatch, pending_by_scope: dict[str, int]) -> None:
    for name, value in (
        ("pending_by_scope", AsyncMock(return_value=pending_by_scope)),
        ("max_synced_at", AsyncMock(return_value=_MOCK_LAST_INDEXED)),
    ):
        monkeypatch.setattr(f"search_api.api.beacon.routes.{name}", value)


def test_status(client: TestClient, monkeypatch):
    """The indexed counts come from the index: ``MockBeaconService.count_indexed``
    serves them from ``_MOCK_INDEXED_COUNTS``, installed for the whole module by
    the ``client`` fixture. The pending counts and the last indexed time come from
    the database: ``_mock_status_database_calls`` mocks the two queries the route
    makes, so no database is needed.

    Every pending document carries a declared scope, since a load rejects one that
    does not, so the scopes account for all of ``documents.pending``.
    """
    _mock_status_database_calls(monkeypatch, {"clinical": 2})

    resp = client.get("/status")

    assert resp.status_code == 200
    assert resp.json() == {
        "deployment": "Bigpicture",
        "documents": {"indexed": _MOCK_INDEXED_COUNTS[None], "pending": 2},
        "scopes": {
            "clinical": {
                "documents": {"indexed": _MOCK_INDEXED_COUNTS["clinical"], "pending": 2}
            },
            "non_clinical": {
                "documents": {
                    "indexed": _MOCK_INDEXED_COUNTS["non_clinical"],
                    "pending": 0,
                }
            },
        },
        "last_indexed": _MOCK_LAST_INDEXED.isoformat().replace("+00:00", "Z"),
    }


def _database_health(monkeypatch, healthy: bool) -> None:
    async def is_healthy() -> bool:
        return healthy

    monkeypatch.setattr("search_api.api.beacon.routes.is_database_healthy", is_healthy)


def test_database_health_ok(client: TestClient, monkeypatch):
    _database_health(monkeypatch, True)

    resp = client.get("/health")

    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_database_health_unhealthy(client: TestClient, monkeypatch):
    _database_health(monkeypatch, False)

    resp = client.get("/health")

    assert resp.status_code == 503
    assert resp.json()["detail"]["database"] == "unhealthy"
    assert resp.json()["detail"]["search"] == "ok"
