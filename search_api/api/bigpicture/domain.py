"""The Bigpicture deployment as a Domain."""

from pathlib import Path

from search_api.api.bigpicture.models import (
    BP_BEACON_ID,
    BP_BEACON_NAME,
    BP_DOMAIN_NAME,
    BP_FILTERING_GROUPS,
    BP_FILTERING_QUALIFIERS,
    BP_FILTERING_SCOPES,
    BP_FILTERING_TERMS,
    BP_NON_FILTERING_FIELDS,
    BP_OPENSEARCH_INDEX,
    BP_SCHEMAS,
    BigpictureBeaconDatasetResultSetsResponse,
    BigpictureBeaconImageResultSetsResponse,
)
from search_api.api.bigpicture.ai import (
    BP_AI_ASSISTANT_DESCRIPTION,
    BP_AI_IMAGE_RESULT_INSTRUCTIONS,
    BP_AI_DATASET_RESULT_INSTRUCTIONS,
    BigpictureAIImageSearchResult,
    BigpictureAIDatasetSearchResult,
)
from search_api.api.bigpicture.opensearch import (
    BigpictureDatasetBeaconService,
    BigpictureImageBeaconService,
)
from search_api.api.domain import BeaconQueryEndpoint, Domain
from search_api.api.opensearch.beacon import OpenSearchBeaconService
from search_api.api.bigpicture.local import BigpictureLocalSource
from search_api.api.bigpicture.remote import BigpictureRemoteSource


BP_LOCAL_SOURCE = BigpictureLocalSource()
BP_REMOTE_SOURCE = BigpictureRemoteSource()

BP_DOMAIN = Domain(
    name=BP_DOMAIN_NAME,
    opensearch_index=BP_OPENSEARCH_INDEX,
    index_file=Path(__file__).parent / "index" / f"{BP_OPENSEARCH_INDEX}.json",
    filtering_terms=BP_FILTERING_TERMS,
    filtering_groups=BP_FILTERING_GROUPS,
    filtering_scopes=BP_FILTERING_SCOPES,
    filtering_qualifiers=BP_FILTERING_QUALIFIERS,
    non_filtering_fields=BP_NON_FILTERING_FIELDS,
    beacon_service_factory=lambda search: OpenSearchBeaconService(
        search,
        BP_OPENSEARCH_INDEX,
        BP_FILTERING_TERMS,
        BP_FILTERING_SCOPES,
        BP_FILTERING_QUALIFIERS,
    ),
    query_endpoints=[
        BeaconQueryEndpoint(
            path="/datasets",
            beacon_service_factory=lambda search: BigpictureDatasetBeaconService(
                search,
                BP_OPENSEARCH_INDEX,
                BP_FILTERING_TERMS,
                BP_FILTERING_SCOPES,
                BP_FILTERING_QUALIFIERS,
            ),
            result_sets_response_model=BigpictureBeaconDatasetResultSetsResponse,
            ai_assistant_description=BP_AI_ASSISTANT_DESCRIPTION,
            ai_result_model=BigpictureAIDatasetSearchResult,
            ai_result_instructions=BP_AI_DATASET_RESULT_INSTRUCTIONS,
        ),
        BeaconQueryEndpoint(
            path="/images",
            beacon_service_factory=lambda search: BigpictureImageBeaconService(
                search,
                BP_OPENSEARCH_INDEX,
                BP_FILTERING_TERMS,
                BP_FILTERING_SCOPES,
                BP_FILTERING_QUALIFIERS,
            ),
            result_sets_response_model=BigpictureBeaconImageResultSetsResponse,
            ai_assistant_description=BP_AI_ASSISTANT_DESCRIPTION,
            ai_result_model=BigpictureAIImageSearchResult,
            ai_result_instructions=BP_AI_IMAGE_RESULT_INSTRUCTIONS,
        ),
    ],
    beacon_id=BP_BEACON_ID,
    beacon_name=BP_BEACON_NAME,
    schemas=BP_SCHEMAS,
    local_source=BP_LOCAL_SOURCE,
    remote_source=BP_REMOTE_SOURCE,
)
