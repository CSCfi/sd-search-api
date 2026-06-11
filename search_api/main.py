from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
import uvicorn

from search_api.api.bigpicture.routes import router as bigpicture_router
from search_api.api.opensearch.services import create_search
from search_api.conf import deployment_config

# uvicorn search_api.main:app --reload

_ROUTERS = {
    "Bigpicture": bigpicture_router,
}

_deployment = deployment_config()
_router = _ROUTERS[_deployment.DEPLOYMENT_TYPE]


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.bp_search = create_search()
    yield
    await app.state.bp_search.close()


app = FastAPI(
    title=f"CSC {_deployment.DEPLOYMENT_TYPE} Beacon",
    version="1.0",
    lifespan=lifespan,
)

app.include_router(_router)


@app.get("/", include_in_schema=False)
async def root() -> RedirectResponse:
    return RedirectResponse(url="/docs")


def main():
    uvicorn.run(app, host="0.0.0.0", port=8000)
