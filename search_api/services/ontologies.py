"""Ontology provider registrations.

Every ontology provider and its preferred term cache is registered here, in
one place, rather than each provider module registering itself on import.
Registration then does not depend on some unrelated module happening to
import the provider. ``api/domain.py`` imports this module, so the registries
are populated before a domain resolves or initialises an ontology.
"""

from search_api.api.beacon.models import SNOMED_ONTOLOGY_ID
from search_api.conf import snomed_term_cache_config
from search_api.services.cached_ontology import CachedOntologyService
from search_api.services.ontology import register_ontology_service
from search_api.services.ontology_term import (
    OntologyTermCacheService,
    PostgresOntologyTermCacheService,
    register_term_cache,
)
from search_api.services.send import SEND_ONTOLOGY_ID, send_ontology_source
from search_api.services.snomed import SnomedService


def _snomed_term_cache() -> OntologyTermCacheService:
    return PostgresOntologyTermCacheService(
        ontology_id=SNOMED_ONTOLOGY_ID,
        refresh_interval=snomed_term_cache_config().SNOMED_CACHE_REFRESH,
    )


def _send_term_cache() -> OntologyTermCacheService:
    return PostgresOntologyTermCacheService(ontology_id=SEND_ONTOLOGY_ID)


register_ontology_service(SNOMED_ONTOLOGY_ID, SnomedService())
register_term_cache(SNOMED_ONTOLOGY_ID, _snomed_term_cache)

register_ontology_service(
    SEND_ONTOLOGY_ID, CachedOntologyService(send_ontology_source())
)
register_term_cache(SEND_ONTOLOGY_ID, _send_term_cache)
