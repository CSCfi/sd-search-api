from collections.abc import Callable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from fastapi import FastAPI
from fastapi.routing import APIRouter

from search_api.api.beacon.services import BeaconService
from search_api.api.opensearch.models import (
    OpenSearchBeaconFilteringTerm,
    OpenSearchField,
)
from search_api.api.opensearch.services import create_search
from search_api.conf import snomed_term_cache_config
from search_api.services.snomed_term import PostgresSnomedTermCacheService


@dataclass(frozen=True)
class Domain:
    """A deployment configuration including the Beacon API and OpenSearch index."""

    name: str  # document-store domain key
    opensearch_index: str
    filtering_terms: Sequence[OpenSearchBeaconFilteringTerm]
    non_filtering_fields: Sequence[OpenSearchField]
    router: APIRouter
    beacon_service_factory: Callable[[Any], BeaconService]

    @property
    def opensearch_fields(self) -> list[OpenSearchField]:
        """All indexed fields (non-filtering first, then filtering terms)."""
        return [*self.non_filtering_fields, *self.filtering_terms]


def make_lifespan(domain: Domain) -> Callable[[FastAPI], Any]:
    """Build the FastAPI lifespan for a domain."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.search = create_search()
        app.state.filtering_terms = domain.filtering_terms
        app.state.beacon_service = domain.beacon_service_factory(app.state.search)

        snomed_term_service = PostgresSnomedTermCacheService(
            refresh_interval=snomed_term_cache_config().SNOMED_CACHE_REFRESH,
        )
        await snomed_term_service.load()
        snomed_term_service.start()
        app.state.snomed_term_service = snomed_term_service

        yield

        snomed_term_service.stop()
        await app.state.search.close()

    return lifespan
