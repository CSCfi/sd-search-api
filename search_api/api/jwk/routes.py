"""The public keys this deployment signs with."""

from typing import Any

from collections.abc import Callable

from fastapi import APIRouter

JWK_PATH = "/jwk"


def make_jwk_router(
    public_jwks: Callable[[], list[dict[str, Any]]] | None,
) -> APIRouter:
    """
    Build a router for publishing public keys to verify JWTs signed by this service.

    :param public_jwks: The deployment's public keys, or None where it signs nothing.
    :return: The router, empty when the deployment signs nothing.
    """

    router = APIRouter(tags=["Keys"])
    if public_jwks is None:
        return router

    @router.get(
        JWK_PATH, summary="The public keys to verify JWTs signed by this service."
    )
    async def get_jwk() -> dict[str, list[dict[str, Any]]]:
        """
        Get the public keys as a JWK Set to verify signed JWTs.

        :return: The JWK Set, empty when this deployment has no key configured.
        """

        return {"keys": public_jwks()}

    return router
