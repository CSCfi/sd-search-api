"""Ontology provider registrations.

Every ontology provider and its preferred term cache is registered here, in
one place, rather than each provider module registering itself on import.
Registration then does not depend on some unrelated module happening to
import the provider. ``api/domain.py`` imports this module, so the registries
are populated before a domain resolves or initialises an ontology.
"""

from search_api.api.beacon.models import SNOMED_ONTOLOGY_ID
from search_api.conf import cache_config
from search_api.services.ontology.cached import CachedOntologyService
from search_api.services.ontology.service import register_ontology_service
from search_api.services.ontology.term_cache import (
    OntologyTermCacheService,
    PostgresOntologyTermCacheService,
    register_term_cache,
)
from search_api.services.ontology.send import SEND_ONTOLOGY_ID, send_ontology_source
from search_api.services.ontology.snomed import SnomedService


def _term_cache(ontology_id: str) -> OntologyTermCacheService:
    return PostgresOntologyTermCacheService(
        ontology_id=ontology_id,
        refresh_interval=cache_config().TERM_CACHE_REFRESH,
    )


register_ontology_service(SNOMED_ONTOLOGY_ID, SnomedService())
register_term_cache(SNOMED_ONTOLOGY_ID, lambda: _term_cache(SNOMED_ONTOLOGY_ID))

register_ontology_service(
    SEND_ONTOLOGY_ID,
    CachedOntologyService(
        send_ontology_source(),
        refresh_interval=cache_config().ONTOLOGY_CACHE_REFRESH,
    ),
)
register_term_cache(SEND_ONTOLOGY_ID, lambda: _term_cache(SEND_ONTOLOGY_ID))
