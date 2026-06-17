from contextlib import asynccontextmanager

from fastapi import FastAPI

from search_api.api.bigpicture.models import (
    BP_FILTERING_TERMS,
    BP_OPENSEARCH_INDEX,
    BP_SNOMED_TABLE,
)
from search_api.api.bigpicture.opensearch import BigpictureOpenSearchBeaconService
from search_api.api.opensearch.services import create_search
from search_api.conf import snomed_term_cache_config
from search_api.services.snomed_term import PostgresSnomedTermCacheService


@asynccontextmanager
async def bigpicture_lifespan(app: FastAPI):
    app.state.search = create_search()
    app.state.filtering_terms = BP_FILTERING_TERMS
    app.state.beacon_service = BigpictureOpenSearchBeaconService(
        app.state.search, BP_OPENSEARCH_INDEX, BP_FILTERING_TERMS
    )

    snomed_term_service = PostgresSnomedTermCacheService(
        table_name=BP_SNOMED_TABLE,
        refresh_interval=snomed_term_cache_config().SNOMED_CACHE_REFRESH,
    )
    await snomed_term_service.load()
    snomed_term_service.start()
    app.state.snomed_term_service = snomed_term_service

    yield

    snomed_term_service.stop()
    await app.state.search.close()
