import secrets

from fastapi import HTTPException, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from search_api.conf import admin_config

_bearer = HTTPBearer()


async def require_admin(
    credentials: HTTPAuthorizationCredentials = Security(_bearer),
) -> None:
    key = admin_config().ADMIN_KEY
    # Compared in constant time, so the response time does not reveal how much of the
    # key matched.
    if key is None or not secrets.compare_digest(
        credentials.credentials.encode(), key.encode()
    ):
        raise HTTPException(status_code=403, detail="Forbidden")
