from fastapi import FastAPI
from fastapi.responses import RedirectResponse
import uvicorn

from search_api.api.admin.routes import router as admin_router
from search_api.api.auth.routes import router as auth_router
from search_api.api.beacon.routes import make_beacon_router
from search_api.api.deployments import get_domain
from search_api.api.jwk.routes import make_jwk_router
from search_api.api.domain import make_lifespan
from search_api.api.exception_handlers import register_exception_handlers
from search_api.api.middlewares import AuthMiddleware
from search_api.conf import admin_config, deployment_config, oidc_config
from search_api.services.access_token import AccessTokenValidator
from search_api.services.auth import AuthServiceHandler
from search_api.services.oidc_metadata import ProviderMetadata

# uvicorn search_api.main:app --reload

_domain = get_domain(deployment_config().DEPLOYMENT_TYPE)

# Required OIDC settings validated here so a misconfigured deployment fails at
# startup instead of only on the first /login or first request carrying a token.
oidc_config()

app = FastAPI(
    title=_domain.beacon_name,
    version="1.0",
    lifespan=make_lifespan(_domain),
)

# One discovery document for the login flow and for validating its tokens.
_provider_metadata = ProviderMetadata()
app.state.auth_service = AuthServiceHandler(_provider_metadata)
app.add_middleware(AuthMiddleware, validator=AccessTokenValidator(_provider_metadata))

app.include_router(make_beacon_router(_domain))
app.include_router(auth_router)
app.include_router(make_jwk_router(_domain.public_jwks))
register_exception_handlers(app)

if admin_config().ADMIN_KEY:
    app.include_router(admin_router)


@app.get("/", include_in_schema=False)
async def root() -> RedirectResponse:
    return RedirectResponse(url="/docs")


def main():
    uvicorn.run(app, host="0.0.0.0", port=8000)
