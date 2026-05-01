"""Pytest fixtures."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

os.environ["POSTGRES_DSN"] = "postgresql://test:test@localhost:5432/test"
os.environ["REDIS_URL"] = "redis://localhost:6379/0"
os.environ["INTERNAL_API_TOKEN"] = "test-token"

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
