from fastapi import FastAPI
from fastapi.responses import RedirectResponse
import uvicorn

from search_api.api.admin.routes import router as admin_router
from search_api.api.bigpicture.domain import BP_DOMAIN
from search_api.api.domain import Domain, make_lifespan
from search_api.api.exception_handlers import register_exception_handlers
from search_api.conf import admin_config, deployment_config

# uvicorn search_api.main:app --reload

# A deployment is associated with a domain.
_DOMAINS: dict[str, Domain] = {
    "Bigpicture": BP_DOMAIN,
}

_deployment = deployment_config()
_domain = _DOMAINS[_deployment.DEPLOYMENT_TYPE]

app = FastAPI(
    title=f"CSC {_domain.name.capitalize()} Beacon",
    version="1.0",
    lifespan=make_lifespan(_domain),
)

app.include_router(_domain.router)
register_exception_handlers(app)

if admin_config().ADMIN_KEY:
    app.include_router(admin_router)


@app.get("/", include_in_schema=False)
async def root() -> RedirectResponse:
    return RedirectResponse(url="/docs")


def main():
    uvicorn.run(app, host="0.0.0.0", port=8000)
