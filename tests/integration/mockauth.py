"""Minimal mock OIDC identity provider for integration tests.

`tests/integration/api/bigpicture/test_routes.py` and `test_routes_ai.py` drive an
already-running sd-search-api server over plain `httpx`, so nothing in that server's
process can be monkeypatched. This module runs a real, separate HTTP server (stdlib
`http.server.ThreadingHTTPServer`) on a fixed local port and speaks just enough OIDC
(discovery, JWKS, authorize, token, userinfo) for the server's idpyoidc RPHandler
to complete a real Authorization Code + PKCE login against it.

The authorize endpoint immediately redirects back to the registered `redirect_uri`
with a fresh code. The token endpoint requires `client_secret_basic` but accepts any
`client_id`/`client_secret`, and issues tokens shaped like LS AAI's: an RFC 9068 access
token (`typ: at+jwt`) and, only when `offline_access` was requested, a JWT refresh token
(`typ: JWT`, no `sub`) that each refresh grant replaces, the spent one being refused.
"""

import base64
import json
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlencode, urlparse

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

HOST = "0.0.0.0"
PORT = 8998

KEY_ID = "mock-oidc-key"

TEST_USER_SUB = "mock-oidc-test-user"
TEST_USER_GIVEN_NAME = "Test"
TEST_USER_FAMILY_NAME = "User"

ID_TOKEN_LIFETIME = 3600
ACCESS_TOKEN_LIFETIME = 3600
REFRESH_TOKEN_LIFETIME = 30 * 24 * 3600


def _b64url_uint(value: int) -> str:
    length = (value.bit_length() + 7) // 8 or 1
    return base64.urlsafe_b64encode(value.to_bytes(length, "big")).rstrip(b"=").decode()


class MockAuthServer(ThreadingHTTPServer):
    """Owns the mock IdP's signing key, JWKS, and in-flight authorization codes."""

    daemon_threads = True

    def __init__(self) -> None:
        super().__init__((HOST, PORT), MockAuthRequestHandler)

        private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        public_numbers = private_key.public_key().public_numbers()

        self.private_key_pem = private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
        self.jwks = {
            "keys": [
                {
                    "kty": "RSA",
                    "use": "sig",
                    "alg": "RS256",
                    "kid": KEY_ID,
                    "n": _b64url_uint(public_numbers.n),
                    "e": _b64url_uint(public_numbers.e),
                }
            ]
        }
        # Authorization code -> (nonce, scope) from the /authorize request that minted it.
        # idpyoidc's client verifies the ID token's `nonce` matches the one it sent.
        self.codes: dict[str, tuple[str, str]] = {}
        # Refresh token -> the scope it was granted. Redeeming one removes it.
        self.refresh_tokens: dict[str, str] = {}
        self.lock = threading.Lock()

    def sign(self, claims: dict, typ: str) -> str:
        return jwt.encode(
            claims,
            self.private_key_pem,
            algorithm="RS256",
            headers={"kid": KEY_ID, "typ": typ},
        )


class MockAuthRequestHandler(BaseHTTPRequestHandler):
    server: MockAuthServer

    def log_message(self, format_str: str, *args: object) -> None:
        pass

    def _get_issuer_url(self) -> str:
        """Derive the issuer URL from the incoming request's Host header.

        This lets the same server be reached as both ``mockauth:8998`` (from the
        API container via the docker network) and ``127.0.0.1:8998`` (from the test
        host), and return a discovery document whose ``issuer`` matches the URL the
        caller used — so idpyoidc's RPHandler always sees a consistent issuer.
        """
        host = self.headers.get("Host", f"localhost:{PORT}")
        return f"http://{host}"

    def _send_json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/.well-known/openid-configuration":
            self._discovery()
        elif parsed.path == "/jwks":
            self._jwks()
        elif parsed.path == "/authorize":
            self._authorize(parse_qs(parsed.query))
        elif parsed.path == "/userinfo":
            self._userinfo()
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/token":
            self._token()
        else:
            self.send_response(404)
            self.end_headers()

    def _discovery(self) -> None:
        issuer = self._get_issuer_url()
        self._send_json(
            {
                "issuer": issuer,
                "authorization_endpoint": f"{issuer}/authorize",
                "token_endpoint": f"{issuer}/token",
                "userinfo_endpoint": f"{issuer}/userinfo",
                "jwks_uri": f"{issuer}/jwks",
                "response_types_supported": ["code"],
                "subject_types_supported": ["public"],
                "id_token_signing_alg_values_supported": ["RS256"],
                "scopes_supported": ["openid", "profile", "email", "offline_access"],
                "grant_types_supported": ["authorization_code", "refresh_token"],
                "token_endpoint_auth_methods_supported": ["client_secret_basic"],
                "code_challenge_methods_supported": ["S256"],
            }
        )

    def _jwks(self) -> None:
        self._send_json(self.server.jwks)

    def _authorize(self, params: dict[str, list[str]]) -> None:
        redirect_uri = params["redirect_uri"][0]
        state = params.get("state", [""])[0]
        nonce = params.get("nonce", [""])[0]
        scope = params.get("scope", [""])[0]

        code = uuid.uuid4().hex
        with self.server.lock:
            self.server.codes[code] = (nonce, scope)

        location = f"{redirect_uri}?{urlencode({'code': code, 'state': state})}"
        self.send_response(303)
        self.send_header("Location", location)
        self.end_headers()

    def _token(self) -> None:
        length = int(self.headers.get("Content-Length", 0))
        form = parse_qs(self.rfile.read(length).decode())
        client_id = self._client_id_from_basic_auth()
        if not client_id:
            self._send_json({"error": "invalid_client"}, status=401)
            return

        grant_type = form.get("grant_type", [""])[0]
        if grant_type == "authorization_code":
            with self.server.lock:
                nonce, scope = self.server.codes.pop(
                    form.get("code", [""])[0], ("", "")
                )
            response = self._issue_tokens(client_id, scope)
            response["id_token"] = self._id_token(client_id, nonce)
        elif grant_type == "refresh_token":
            with self.server.lock:
                scope = self.server.refresh_tokens.pop(
                    form.get("refresh_token", [""])[0], None
                )
            if scope is None:
                self._send_json({"error": "invalid_grant"}, status=400)
                return
            response = self._issue_tokens(client_id, scope)
        else:
            self._send_json({"error": "unsupported_grant_type"}, status=400)
            return

        self._send_json(response)

    def _issue_tokens(self, client_id: str, scope: str) -> dict:
        """An RFC 9068 access token, plus a refresh token if `offline_access` was asked."""
        now = int(time.time())
        access_token = self.server.sign(
            {
                "iss": self._get_issuer_url(),
                "sub": TEST_USER_SUB,
                "aud": client_id,
                "client_id": client_id,
                "scope": scope,
                "iat": now,
                "exp": now + ACCESS_TOKEN_LIFETIME,
                "jti": uuid.uuid4().hex,
            },
            typ="at+jwt",
        )
        response = {
            "access_token": access_token,
            "token_type": "Bearer",
            "expires_in": ACCESS_TOKEN_LIFETIME,
            "scope": scope,
        }
        if "offline_access" in scope.split():
            refresh_token = self.server.sign(
                {
                    "iss": self._get_issuer_url(),
                    "aud": client_id,
                    "exp": now + REFRESH_TOKEN_LIFETIME,
                    "jti": uuid.uuid4().hex,
                },
                typ="JWT",
            )
            with self.server.lock:
                self.server.refresh_tokens[refresh_token] = scope
            response["refresh_token"] = refresh_token
        return response

    def _id_token(self, client_id: str, nonce: str) -> str:
        now = int(time.time())
        return self.server.sign(
            {
                "iss": self._get_issuer_url(),
                "sub": TEST_USER_SUB,
                "aud": client_id,
                "exp": now + ID_TOKEN_LIFETIME,
                "iat": now,
                "nonce": nonce,
            },
            typ="JWT",
        )

    def _client_id_from_basic_auth(self) -> str:
        # client_secret_basic puts client_id/secret in the Authorization header. This
        # mock trusts the client_id and skips verifying the secret (fixed test
        # credentials, accepted unconditionally).
        auth_header = self.headers.get("Authorization", "")
        if not auth_header.lower().startswith("basic "):
            return ""
        decoded = base64.b64decode(auth_header.split(" ", 1)[1]).decode()
        client_id, _, _ = decoded.partition(":")
        return client_id

    def _userinfo(self) -> None:
        self._send_json(
            {
                "sub": TEST_USER_SUB,
                "given_name": TEST_USER_GIVEN_NAME,
                "family_name": TEST_USER_FAMILY_NAME,
            }
        )


class MockAuthProvider:
    """Starts/stops the mock IdP HTTP server in a background thread."""

    def __init__(self) -> None:
        self._server = MockAuthServer()
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join()


if __name__ == "__main__":
    provider = MockAuthProvider()
    print(f"Starting mock OIDC provider on 0.0.0.0:{PORT}...", flush=True)
    provider.start()
    print("Mock OIDC provider is running", flush=True)
    provider._thread.join()
