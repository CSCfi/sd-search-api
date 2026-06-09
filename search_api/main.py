from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
import uvicorn

from search_api.api.bigpicture.routes import router
from search_api.api.opensearch.services import create_search

# uvicorn search_api.main:app --reload

# TODO(improve): support other than Bigpicture Beacons


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.bp_search = create_search()
    yield
    await app.state.bp_search.close()


app = FastAPI(title="CSC Bigpicture Beacon", version="1.0", lifespan=lifespan)

app.include_router(router)


@app.get("/", include_in_schema=False)
async def root() -> RedirectResponse:
    return RedirectResponse(url="/docs")


def main():
    uvicorn.run(app, host="0.0.0.0", port=8000)
