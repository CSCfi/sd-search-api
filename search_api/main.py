from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
import uvicorn

from search_api.api.bigpicture.models import BP_SNOMED_TABLE
from search_api.api.bigpicture.routes import router as bigpicture_router
from search_api.api.opensearch.services import create_search
from search_api.api.admin.routes import router as admin_router
from search_api.conf import admin_config, deployment_config, snomed_term_cache_config
from search_api.services.snomed_term import PostgresSnomedTermCacheService

# uvicorn search_api.main:app --reload

_ROUTERS = {
    "Bigpicture": bigpicture_router,
}

_deployment = deployment_config()
_router = _ROUTERS[_deployment.DEPLOYMENT_TYPE]


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.bp_search = create_search()

    snomed_term_service = PostgresSnomedTermCacheService(
        table_name=BP_SNOMED_TABLE,
        refresh_interval=snomed_term_cache_config().SNOMED_CACHE_REFRESH,
    )
    await snomed_term_service.load()
    snomed_term_service.start()
    app.state.snomed_term_service = snomed_term_service

    yield

    snomed_term_service.stop()
    await app.state.bp_search.close()


app = FastAPI(
    title=f"CSC {_deployment.DEPLOYMENT_TYPE} Beacon",
    version="1.0",
    lifespan=lifespan,
)

app.include_router(_router)

if admin_config().ADMIN_KEY:
    app.include_router(admin_router)


@app.get("/", include_in_schema=False)
async def root() -> RedirectResponse:
    return RedirectResponse(url="/docs")


def main():
    uvicorn.run(app, host="0.0.0.0", port=8000)
