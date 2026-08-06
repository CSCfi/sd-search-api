from fastapi import FastAPI
from fastapi.responses import RedirectResponse
import uvicorn

from search_api.api.admin.routes import router as admin_router
from search_api.api.auth.routes import router as auth_router
from search_api.api.beacon.routes import make_beacon_router
from search_api.api.deployments import get_domain
from search_api.api.domain import make_lifespan
from search_api.api.exception_handlers import register_exception_handlers
from search_api.api.middlewares import AuthMiddleware
from search_api.conf import admin_config, deployment_config, jwt_config, oidc_config
from search_api.services.auth import AuthServiceHandler

# uvicorn search_api.main:app --reload

_domain = get_domain(deployment_config().DEPLOYMENT_TYPE)

# Required OIDC/JWT settings validated here so a misconfigured deployment fails
# at startup instead of only on the first /login or first request carrying a token.
oidc_config()
jwt_config()

app = FastAPI(
    title=f"CSC {_domain.name.capitalize()} Beacon",
    version="1.0",
    lifespan=make_lifespan(_domain),
)

app.state.auth_service = AuthServiceHandler()
app.add_middleware(AuthMiddleware)

app.include_router(make_beacon_router(_domain))
app.include_router(auth_router)
register_exception_handlers(app)

if admin_config().ADMIN_KEY:
    app.include_router(admin_router)


@app.get("/", include_in_schema=False)
async def root() -> RedirectResponse:
    return RedirectResponse(url="/docs")


def main():
    uvicorn.run(app, host="0.0.0.0", port=8000)
