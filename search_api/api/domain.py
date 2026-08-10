import argparse
from collections.abc import Callable, Iterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

from fastapi import FastAPI

from search_api.ai.models import AISearchResult
from search_api.api.beacon.models import (
    BeaconFilteringGroup,
    BeaconFilteringQualifier,
    BeaconFilteringScope,
    BeaconResultSetsResponse,
)
from search_api.api.beacon.services import BeaconService
from search_api.api.opensearch.models import (
    ExtractedDocument,
    OpenSearchBeaconFilteringTerm,
    OpenSearchField,
)
from search_api.api.opensearch.services import create_search
from search_api.conf import cache_config
from search_api.api.qualifiers import qualifier_fields
from search_api.api.scopes import scope_field
from search_api.services.ontology.service import (
    get_ontology_id_by_field,
    get_ontology_service,
)
from search_api.services.value_counts import ValueCountsUpdater
from search_api.services.ontology.term_cache import (
    OntologyTermCache,
    create_term_caches,
)

# Imported for its side effects: populates the ontology service and term cache
# registries that this module looks up below.
import search_api.services.ontology.registrations  # noqa: F401

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
    filtering_groups: Sequence[BeaconFilteringGroup]
    filtering_scopes: Sequence[BeaconFilteringScope]
    filtering_qualifiers: Sequence[BeaconFilteringQualifier]
    non_filtering_fields: Sequence[OpenSearchField]
    loader: Loader[Any]
    beacon_service_factory: Callable[[Any], BeaconService]
    beacon_id: str
    beacon_name: str
    schemas: Sequence[str]  # Beacon entity types (returnedSchemas).
    result_sets_response_model: type[BeaconResultSetsResponse[Any]]
    ai_assistant_description: str
    ai_result_model: type[AISearchResult]
    ai_result_instructions: str

    @property
    def nested_groups(self) -> set[str]:
        """The nested groups the filtering terms place fields in."""
        return {term.group for term in self.filtering_terms if term.group is not None}

    @property
    def opensearch_fields(self) -> list[OpenSearchField]:
        """Every field indexed for this deployment.

        Filtering terms, index-only fields, the scope field, and
        the qualifiers field of every nested group.
        """
        scope = [scope_field()] if self.filtering_scopes else []
        return [
            *scope,
            *self.non_filtering_fields,
            *self.filtering_terms,
            *qualifier_fields(self.nested_groups),
        ]

    @property
    def ontology_id_by_field(self) -> dict[str, str]:
        """Map each ontology filtering term's id to its ontology id (e.g. ``SCTID``)."""
        return get_ontology_id_by_field(self.filtering_terms)

    @property
    def ontology_ids(self) -> set[str]:
        """Distinct ontology ids referenced by the filtering terms."""
        return set(self.ontology_id_by_field.values())


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
        ontology_term_services: dict[str, OntologyTermCache] = create_term_caches(
            domain.ontology_ids
        )
        for term_service in ontology_term_services.values():
            await term_service.start()
        app.state.ontology_term_services = ontology_term_services

        # Initialise ontology services used by the domain.
        ontology_services = [
            get_ontology_service(ontology_id) for ontology_id in domain.ontology_ids
        ]
        for ontology_service in ontology_services:
            await ontology_service.init()
            await ontology_service.start()

        # Fill the value count cache and keep it updated.
        value_counts = ValueCountsUpdater(
            app.state.beacon_service,
            refresh_interval=cache_config().VALUE_COUNT_CACHE_REFRESH,
        )
        await value_counts.start()

        yield

        value_counts.stop()
        for ontology_service in ontology_services:
            ontology_service.stop()
        for term_service in ontology_term_services.values():
            term_service.stop()
        await app.state.search.close()

    return lifespan
