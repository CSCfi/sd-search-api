"""Ontology provider registrations.

Every ontology provider is registered here, in one place, rather than each
provider module registering itself on import. Registration then does not depend
on some unrelated module happening to import the provider. ``api/domain.py``
imports this module, so the registry is populated before a domain resolves or
initialises an ontology.
"""

from search_api.api.beacon.models import SNOMED_ONTOLOGY_ID
from search_api.conf import cache_config
from search_api.services.ontology.cache.service import CachedOntologyService
from search_api.services.ontology.service import register_ontology_service
from search_api.services.ontology.cache.store import OntologyCacheStore
from search_api.services.ontology.send import SEND_ONTOLOGY_ID, SendOntologySource
from search_api.services.ontology.snomed import SnomedService

register_ontology_service(SNOMED_ONTOLOGY_ID, SnomedService())

register_ontology_service(
    SEND_ONTOLOGY_ID,
    CachedOntologyService(
        OntologyCacheStore(SEND_ONTOLOGY_ID),
        SendOntologySource(),
        refresh_interval=cache_config().ONTOLOGY_CACHE_REFRESH,
    ),
)
