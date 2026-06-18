"""The Bigpicture deployment as a Domain."""

from search_api.api.bigpicture.models import (
    BP_DOMAIN_NAME,
    BP_FILTERING_TERMS,
    BP_NON_FILTERING_FIELDS,
    BP_OPENSEARCH_INDEX,
)
from search_api.api.bigpicture.opensearch import BigpictureOpenSearchBeaconService
from search_api.api.bigpicture.routes import router as bigpicture_router
from search_api.api.domain import Domain

BP_DOMAIN = Domain(
    name=BP_DOMAIN_NAME,
    opensearch_index=BP_OPENSEARCH_INDEX,
    filtering_terms=BP_FILTERING_TERMS,
    non_filtering_fields=BP_NON_FILTERING_FIELDS,
    router=bigpicture_router,
    beacon_service_factory=lambda search: BigpictureOpenSearchBeaconService(
        search, BP_OPENSEARCH_INDEX, BP_FILTERING_TERMS
    ),
)
