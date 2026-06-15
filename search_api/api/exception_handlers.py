import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from search_api.exceptions import SystemException, UserException

logger = logging.getLogger(__name__)


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(UserException, _user_exception_handler)
    app.add_exception_handler(SystemException, _system_exception_handler)


async def _user_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, UserException)
    logger.exception("User error.", exc_info=exc)
    return JSONResponse(status_code=400, content={"detail": str(exc)})


async def _system_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, SystemException)
    logger.exception("System error.", exc_info=exc)
    return JSONResponse(status_code=503, content={"detail": "Service error."})
