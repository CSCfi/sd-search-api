"""Integration tests for the AI filters endpoint."""

from urllib.parse import urlparse, urlunparse

import httpx
import pytest

from search_api.ai.models import AIInterpretation
from search_api.api.beacon.models import BeaconQueryFilter
from search_api.conf import feature_config
from tests.integration.mockauth import PORT as OIDC_MOCK_PORT

requires_feature_ai = pytest.mark.skipif(
    not feature_config().FEATURE_AI, reason="Requires FEATURE_AI=true"
)


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


@requires_feature_ai
def test_ai_filters_returns_filters(client: httpx.Client):
    resp = client.post(
        "/ai/filters", json={"query": "images for human females"}, timeout=60.0
    )
    assert resp.status_code == 200
    result = AIInterpretation.model_validate(resp.json())
    assert len(result.interpretation) > 0
    assert len(result.filters) in (1, 2)
    assert BeaconQueryFilter(id="sex", value="Female") in result.filters
    if len(result.filters) == 2:
        # Ontology values are returned as concept ids: Homo sapiens.
        assert (
            BeaconQueryFilter(id="animal_species", value=["337915000"])
            in result.filters
        )


@requires_feature_ai
def test_ai_filters_missing_body_returns_422(client: httpx.Client):
    resp = client.post("/ai/filters", json={})
    assert resp.status_code == 422
