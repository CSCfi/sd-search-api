from typing import Any

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.routing import APIRouter
from pydantic import BaseModel
import uvicorn

from search_api.api.admin.routes import router as admin_router
from search_api.api.bigpicture.lifespan import bigpicture_lifespan
from search_api.api.bigpicture.routes import router as bigpicture_router
from search_api.api.exception_handlers import register_exception_handlers
from search_api.conf import admin_config, deployment_config

# uvicorn search_api.main:app --reload


class RouterConfig(BaseModel):
    model_config = {"arbitrary_types_allowed": True}

    router: APIRouter
    lifespan: Any


_DEPLOYMENTS: dict[str, RouterConfig] = {
    "Bigpicture": RouterConfig(router=bigpicture_router, lifespan=bigpicture_lifespan),
}

_deployment = deployment_config()
_config = _DEPLOYMENTS[_deployment.DEPLOYMENT_TYPE]

app = FastAPI(
    title=f"CSC {_deployment.DEPLOYMENT_TYPE} Beacon",
    version="1.0",
    lifespan=_config.lifespan,
)

app.include_router(_config.router)
register_exception_handlers(app)

if admin_config().ADMIN_KEY:
    app.include_router(admin_router)


@app.get("/", include_in_schema=False)
async def root() -> RedirectResponse:
    return RedirectResponse(url="/docs")


def main():
    uvicorn.run(app, host="0.0.0.0", port=8000)
