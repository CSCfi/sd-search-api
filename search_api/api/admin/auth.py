from fastapi import HTTPException, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from search_api.conf import admin_config

_bearer = HTTPBearer()


async def require_admin(
    credentials: HTTPAuthorizationCredentials = Security(_bearer),
) -> None:
    if credentials.credentials != admin_config().ADMIN_KEY:
        raise HTTPException(status_code=403, detail="Forbidden")
