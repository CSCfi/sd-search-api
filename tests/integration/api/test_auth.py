"""Integration tests for the session: LS AAI-shaped tokens from the mock provider.

Each test logs in on its own client, since refreshing replaces the session cookies
and spends the refresh token.
"""

import os
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import parse_qs, urlparse

import httpx
import jwt
import pytest

from tests.integration.login import login
from tests.integration.mockauth import PORT as OIDC_MOCK_PORT

API_URL = "http://localhost:8000"
PROTECTED_PATH = "/status"


@pytest.fixture
def client() -> Iterator[httpx.Client]:
    with httpx.Client(base_url=API_URL, follow_redirects=False, timeout=30.0) as c:
        yield c


def _cookie(client: httpx.Client, name: str) -> tuple[str, str]:
    """The cookie's value and the path it was set for."""
    (cookie,) = [c for c in client.cookies.jar if c.name == name]
    assert cookie.value is not None
    return cookie.value, cookie.path


def _status_with(headers: dict[str, str]) -> int:
    with httpx.Client(base_url=API_URL, timeout=30.0) as anonymous:
        return anonymous.get(PROTECTED_PATH, headers=headers).status_code


def _bearer_status(token: str) -> int:
    return _status_with({"Authorization": f"Bearer {token}"})


def _get_with_cookie(cookie: str) -> httpx.Response:
    """A protected path, from a client holding only the cookies in `cookie`."""
    with httpx.Client(base_url=API_URL, timeout=30.0) as anonymous:
        return anonymous.get(PROTECTED_PATH, headers={"Cookie": cookie})


def _set_cookie_paths(response: httpx.Response, name: str) -> dict[str, str]:
    """The response's Set-Cookie headers for `name`, keyed by path."""
    return {
        header.split("Path=", 1)[1].split(";", 1)[0]: header
        for header in response.headers.get_list("set-cookie")
        if header.startswith(f"{name}=")
    }


def _tokens_from_mock(issuer_host: str, client_id: str | None = None) -> dict:
    """Run a code grant against the mock directly, as another relying party would.

    `Host` is the one the API reaches the mock at, so the tokens' `iss` is the
    issuer the API trusts.
    """
    mock = f"http://127.0.0.1:{OIDC_MOCK_PORT}"
    headers = {"Host": issuer_host}
    authorize = httpx.get(
        f"{mock}/authorize",
        params={
            "redirect_uri": "http://localhost/cb",
            "state": "s",
            "nonce": "n",
            "scope": "openid offline_access",
        },
        headers=headers,
    )
    code = parse_qs(urlparse(authorize.headers["location"]).query)["code"][0]
    response = httpx.post(
        f"{mock}/token",
        data={"grant_type": "authorization_code", "code": code},
        auth=(
            client_id or os.environ["OIDC_CLIENT_ID"],
            os.environ["OIDC_CLIENT_SECRET"],
        ),
        headers=headers,
    )
    assert response.status_code == 200
    return response.json()


def test_login_sets_the_providers_access_and_refresh_tokens(client):
    login(client)

    access_token, access_path = _cookie(client, "access_token")
    assert access_path == "/"
    assert jwt.get_unverified_header(access_token)["typ"] == "at+jwt"
    assert (
        jwt.decode(access_token, options={"verify_signature": False})["aud"]
        == (os.environ["OIDC_CLIENT_ID"])
    )

    refresh_token, refresh_path = _cookie(client, "refresh_token")
    assert refresh_path == "/"
    assert jwt.get_unverified_header(refresh_token)["typ"] == "JWT"

    # The same token authenticates by cookie and as a bearer token, and a cookie of
    # someone else's that a strict parser rejects does not hide it.
    assert client.get(PROTECTED_PATH).status_code == 200
    assert _bearer_status(access_token) == 200
    cookies = f'cc_cookie={{"categories":["necessary"]}}; access_token={access_token}'
    assert _status_with({"Cookie": cookies}) == 200


def test_refresh_replaces_both_cookies(client):
    login(client)
    old_access, _ = _cookie(client, "access_token")
    old_refresh, _ = _cookie(client, "refresh_token")

    response = client.post("/refresh")

    assert response.status_code == 204
    new_access, _ = _cookie(client, "access_token")
    new_refresh, refresh_path = _cookie(client, "refresh_token")
    assert new_access != old_access
    assert new_refresh != old_refresh
    assert refresh_path == "/"
    assert client.get(PROTECTED_PATH).status_code == 200


def test_tab_losing_a_refresh_race_gets_the_winners_cookies(client):
    login(client)
    spent, _ = _cookie(client, "refresh_token")

    # Tab A refreshes first; LS AAI replaces the refresh token.
    assert client.post("/refresh").status_code == 204
    winners, _ = _cookie(client, "refresh_token")

    # Tab B sent the replaced token at the same moment: it is not redeemed again,
    # which the provider would refuse, but given tab A's result.
    with httpx.Client(base_url=API_URL, timeout=30.0) as other_tab:
        response = other_tab.post(
            "/refresh", headers={"Cookie": f"refresh_token={spent}"}
        )

    assert response.status_code == 204
    assert response.cookies.get("refresh_token") == winners
    # Tab A's new cookies, shared by every tab, still work.
    assert client.get(PROTECTED_PATH).status_code == 200
    assert client.post("/refresh").status_code == 204


@pytest.mark.parametrize(
    "access_cookie",
    ["", "access_token=expired-or-forged; "],
    ids=["no-access-token", "invalid-access-token"],
)
def test_session_is_renewed_inline_from_the_refresh_cookie(client, access_cookie):
    # The browser dropped the access cookie when it expired; the refresh cookie lasts.
    login(client)
    refresh_token, _ = _cookie(client, "refresh_token")

    response = _get_with_cookie(f"{access_cookie}refresh_token={refresh_token}")

    assert response.status_code == 200
    new_access = response.cookies.get("access_token")
    new_refresh = response.cookies.get("refresh_token")
    assert new_access is not None
    assert new_refresh not in (None, refresh_token)
    assert set(_set_cookie_paths(response, "refresh_token")) == {"/"}
    assert _bearer_status(new_access) == 200


def test_parallel_requests_renew_the_session_once(client):
    # The provider refuses a spent refresh token, so a second redemption would be a 401.
    login(client)
    refresh_token, _ = _cookie(client, "refresh_token")

    with ThreadPoolExecutor(max_workers=10) as pool:
        responses = list(
            pool.map(
                lambda _: _get_with_cookie(f"refresh_token={refresh_token}"), range(10)
            )
        )

    assert [r.status_code for r in responses] == [200] * 10
    renewed = {r.cookies.get("refresh_token") for r in responses}
    assert len(renewed) == 1
    assert renewed != {refresh_token}


def test_rejected_refresh_token_is_401_leaving_the_cookies_alone(client):
    login(client)
    refresh_token, _ = _cookie(client, "refresh_token")
    assert client.delete("/refresh").status_code == 204  # revoked at the provider

    response = _get_with_cookie(f"refresh_token={refresh_token}")

    assert response.status_code == 401
    assert "set-cookie" not in response.headers


def test_newest_of_two_refresh_cookies_is_redeemed(client):
    login(client)
    spent, _ = _cookie(client, "refresh_token")
    assert client.post("/refresh").status_code == 204
    current, _ = _cookie(client, "refresh_token")

    # A browser holding both lists the one at the longer path, `/refresh`, first.
    with httpx.Client(base_url=API_URL, timeout=30.0) as browser:
        response = browser.post(
            "/refresh",
            headers={"Cookie": f"refresh_token={spent}; refresh_token={current}"},
        )

    assert response.status_code == 204
    # The current token was redeemed: the spent one would have given it back.
    assert response.cookies.get("refresh_token") not in (spent, current)


def test_only_access_tokens_are_accepted(client):
    # The ID and refresh tokens share the access token's key, `iss` and `aud`.
    tokens = _tokens_from_mock(login(client))

    assert _bearer_status(tokens["id_token"]) == 401
    assert _bearer_status(tokens["refresh_token"]) == 401
    # The access token from the same response is one: the check is `typ`, not origin.
    assert _bearer_status(tokens["access_token"]) == 200


def test_access_token_issued_to_another_client_is_rejected(client):
    tokens = _tokens_from_mock(login(client), client_id="another-relying-party")

    assert _bearer_status(tokens["access_token"]) == 401


def test_logout_clears_both_cookies(client):
    login(client)

    assert client.get("/logout").status_code == 303

    assert {c.name for c in client.cookies.jar} & {"access_token", "refresh_token"} == (
        set()
    )
    assert client.get(PROTECTED_PATH).status_code == 401


def test_delete_refresh_revokes_the_refresh_token(client):
    login(client)
    refresh_token, _ = _cookie(client, "refresh_token")

    response = client.delete("/refresh")

    assert response.status_code == 204
    assert set(_set_cookie_paths(response, "refresh_token")) == {"/"}
    assert {c.name for c in client.cookies.jar} & {"access_token", "refresh_token"} == (
        set()
    )
    with httpx.Client(base_url=API_URL, timeout=30.0) as other_tab:
        response = other_tab.post(
            "/refresh", headers={"Cookie": f"refresh_token={refresh_token}"}
        )
    assert response.status_code == 401
