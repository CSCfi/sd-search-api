import argparse
from collections.abc import Callable, Iterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

from fastapi import FastAPI

from search_api.api.beacon.models import BeaconResultSetsResponse
from search_api.api.beacon.services import BeaconService
from search_api.api.opensearch.models import (
    ExtractedDocument,
    OpenSearchBeaconFilteringTerm,
    OpenSearchField,
)
from search_api.api.opensearch.services import create_search
from search_api.services.ontology_term import (
    OntologyTermCacheService,
    create_term_caches,
)

LoadOptionsT = TypeVar("LoadOptionsT")


@dataclass(frozen=True)
class Loader(Generic[LoadOptionsT]):
    """How a deployment loads its source data, parameterised by its options."""

    add_load_options: Callable[[argparse.ArgumentParser], None]
    parse_load_options: Callable[[argparse.Namespace], LoadOptionsT]
    extract: Callable[[LoadOptionsT], Iterator[ExtractedDocument]]


@dataclass(frozen=True)
class Domain:
    """A deployment configuration including the Beacon API and OpenSearch index."""

    name: str  # document-store domain key
    opensearch_index: str
    filtering_terms: Sequence[OpenSearchBeaconFilteringTerm]
    non_filtering_fields: Sequence[OpenSearchField]
    loader: Loader[Any]
    beacon_service_factory: Callable[[Any], BeaconService]
    beacon_id: str
    beacon_name: str
    schemas: Sequence[str]  # Beacon entity types (returnedSchemas).
    result_sets_response_model: type[BeaconResultSetsResponse[Any]]
    ai_assistant_description: str

    @property
    def opensearch_fields(self) -> list[OpenSearchField]:
        """All indexed fields (non-filtering first, then filtering terms)."""
        return [*self.non_filtering_fields, *self.filtering_terms]

    @property
    def ontology_ids(self) -> set[str]:
        """Distinct ontology ids referenced by the filtering terms."""
        return {
            t.ontology.id
            for t in self.filtering_terms
            if t.type in ("ontology", "ontologyOrValue") and t.ontology is not None
        }


def make_lifespan(domain: Domain) -> Callable[[FastAPI], Any]:
    """Build the FastAPI lifespan for a domain."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.domain = domain
        app.state.search = create_search()
        app.state.filtering_terms = domain.filtering_terms
        app.state.beacon_service = domain.beacon_service_factory(app.state.search)

        # One term cache per ontology created automatically from the
        # registered providers.
        ontology_term_services: dict[str, OntologyTermCacheService] = (
            create_term_caches(domain.ontology_ids)
        )
        for term_service in ontology_term_services.values():
            await term_service.load()
            term_service.start()
        app.state.ontology_term_services = ontology_term_services

        yield

        for term_service in ontology_term_services.values():
            term_service.stop()
        await app.state.search.close()

    return lifespan
