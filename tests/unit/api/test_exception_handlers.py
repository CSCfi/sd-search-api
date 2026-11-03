"""Unit tests for exception handlers."""

import logging
from unittest.mock import MagicMock

import pytest

from search_api.api.exception_handlers import (
    _system_exception_handler,
    _user_exception_handler,
)
from search_api.exceptions import SystemException, UserException


@pytest.mark.asyncio
async def test_user_exception(caplog: pytest.LogCaptureFixture):
    with caplog.at_level(logging.INFO):
        response = await _user_exception_handler(MagicMock(), UserException("test"))
    assert response.status_code == 400
    assert response.body == b'{"detail":"test"}'
    # A user error is logged as info without a traceback.
    (record,) = caplog.records
    assert (record.levelno, record.getMessage()) == (logging.INFO, "User error: test")
    assert record.exc_info is None


@pytest.mark.asyncio
async def test_system_exception():
    response = await _system_exception_handler(MagicMock(), SystemException("test"))
    assert response.status_code == 503
    assert response.body == b'{"detail":"Service error."}'
