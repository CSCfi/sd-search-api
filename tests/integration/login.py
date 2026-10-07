"""Log an `httpx.Client` in to the running API through the mock OIDC provider."""

from urllib.parse import urlparse, urlunparse

import httpx

from tests.integration.mockauth import PORT as OIDC_MOCK_PORT


def login(client: httpx.Client) -> str:
    """Run the whole login flow, leaving the session cookies in `client`.

    Returns the host the API reaches the provider at (e.g. `mockauth:8998`), which is
    also what the mock puts in `iss`, for tests that ask the mock for tokens directly.
    """
    # Step 1: Initiate login - store the oidc_state cookie and get the IdP auth URL.
    login_resp = client.get("/login")
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
    callback_path = parsed_cb.path + ("?" + parsed_cb.query if parsed_cb.query else "")
    final_resp = client.get(callback_path, follow_redirects=True)
    assert final_resp.status_code == 200
    assert client.cookies.get("access_token") is not None
    return parsed_auth.netloc
