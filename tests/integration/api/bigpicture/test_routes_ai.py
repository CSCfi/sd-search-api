"""Integration tests for the AI search endpoint. Requires Ollama running locally."""

from urllib.parse import urlparse, urlunparse

import httpx
import pytest

from search_api.ai.models import AIQueryFilter
from search_api.api.bigpicture.ai import BigpictureAISearchResult
from tests.integration.mockauth import PORT as OIDC_MOCK_PORT

skip = pytest.mark.skip(reason="Requires Ollama")


@pytest.fixture(scope="module")
def client() -> httpx.Client:
    with httpx.Client(base_url="http://localhost:8000", follow_redirects=False) as c:
        login_resp = c.get("/login")
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


@skip
def test_ai_query_returns_result(client: httpx.Client):
    resp = client.post(
        "/ai/query", json={"query": "images for human females"}, timeout=60.0
    )
    assert resp.status_code == 200
    result = BigpictureAISearchResult.model_validate(resp.json())
    assert isinstance(result.interpretation, str)
    assert len(result.interpretation) > 0
    assert result.dataset_count >= 0
    assert isinstance(result.datasets, list)

    assert result.dataset_count == 1
    assert len(result.datasets) == 1
    dataset: BigpictureAISearchResult.Dataset = result.datasets[0]
    assert dataset.dataset_id == "testDataset"
    assert dataset.dataset_title == "testTitle"
    assert dataset.total_image_count == 1
    assert dataset.matching_image_count == 1
    assert len(result.filters) in (1, 2)
    assert AIQueryFilter(id="sex", value="Female") in result.filters
    if len(result.filters) == 2:
        assert AIQueryFilter(id="animal_species", value="human") in result.filters


@skip
def test_ai_query_missing_body_returns_422(client: httpx.Client):
    resp = client.post("/ai/query", json={})
    assert resp.status_code == 422
