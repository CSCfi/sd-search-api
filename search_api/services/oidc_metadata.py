"""The OIDC issuer's discovery document."""

import asyncio
from typing import Any

import httpx

from search_api.conf import oidc_config
from search_api.exceptions import SystemException

DISCOVERY_TIMEOUT = 10.0


class ProviderMetadata:
    """The issuer's discovery document, fetched on first use and then kept.

    Fetched lazily so that the server starts while the issuer is unreachable, and kept
    for the life of the process: endpoints and the issuer do not move under a running
    deployment, and signing-key rotation is handled by the JWKS client, not here. A
    failed fetch is not kept, so the next caller tries again.
    """

    def __init__(self) -> None:
        self._metadata: dict[str, Any] | None = None
        self._lock = asyncio.Lock()

    async def get(self) -> dict[str, Any]:
        """Return the discovery document, or raise `SystemException` if unreachable."""
        if self._metadata is None:
            async with self._lock:
                if self._metadata is None:
                    self._metadata = await self._fetch()
        return self._metadata

    @staticmethod
    async def _fetch() -> dict[str, Any]:
        url = oidc_config().OIDC_URL.rstrip("/") + "/.well-known/openid-configuration"
        try:
            async with httpx.AsyncClient(timeout=DISCOVERY_TIMEOUT) as client:
                response = await client.get(url)
                response.raise_for_status()
                metadata = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise SystemException("OIDC discovery failed.") from exc

        missing = {"issuer", "jwks_uri", "token_endpoint"} - set(metadata)
        if missing:
            raise SystemException(
                f"OIDC discovery document is missing {sorted(missing)}."
            )
        return metadata
