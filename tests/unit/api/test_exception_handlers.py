"""Unit tests for exception handlers."""

from unittest.mock import MagicMock

import pytest

from search_api.api.exception_handlers import (
    _system_exception_handler,
    _user_exception_handler,
)
from search_api.exceptions import SystemException, UserException


@pytest.mark.asyncio
async def test_user_exception():
    response = await _user_exception_handler(MagicMock(), UserException("test"))
    assert response.status_code == 400
    assert response.body == b'{"detail":"test"}'


@pytest.mark.asyncio
async def test_system_exception():
    response = await _system_exception_handler(MagicMock(), SystemException("test"))
    assert response.status_code == 503
    assert response.body == b'{"detail":"Service error."}'
