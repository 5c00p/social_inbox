"""Pytest fixtures.

Pattern: each test gets a fresh pool in its own event loop. Data is cleaned
up after each test via TRUNCATE. Migrations are applied once and then skipped
(idempotent via _migrations tracking table).

The test database is `social_inbox_test`, separate from the main DB.
It's created by docker/postgres-init.sh on first Postgres startup.
"""
from __future__ import annotations

import os
from collections.abc import AsyncIterator

import asyncpg
import pytest
from httpx import ASGITransport, AsyncClient

# --- IMPORTANT: override env BEFORE importing app modules ---
# Test DB DSN. Default assumes docker compose stack running locally
# with the postgres-init.sh script having created `social_inbox_test`.
TEST_DSN = os.environ.get(
    "TEST_POSTGRES_DSN",
    "postgresql://social_inbox:social_inbox@localhost:5432/social_inbox_test",
)

os.environ["POSTGRES_DSN"] = TEST_DSN
os.environ["REDIS_URL"] = "redis://localhost:6379/1"  # different DB index than prod
os.environ["INTERNAL_API_TOKEN"] = "test-token"
os.environ["MESSAGING_PROVIDER"] = "sendpulse"
# Reset cached settings (lru_cache) — will be re-evaluated on next call.
from app.config import get_settings  # noqa: E402

get_settings.cache_clear()

from app.main import app  # noqa: E402
from app.repos.pool import close_pool, get_pool, run_migrations  # noqa: E402
from app.repos.redis_client import close_redis  # noqa: E402
from app.workers.enqueue import close_arq  # noqa: E402

# Tables to truncate between tests (CASCADE handles dependent tables).
_TRUNCATE_TABLES = "social_users, scenarios, events_log, comment_replies_dedup"


@pytest.fixture(autouse=True)
async def _cleanup_singletons() -> AsyncIterator[None]:
    """Close Redis and arq singletons after every test.

    Prevents "Event loop is closed" errors when a singleton is created in one
    test's function-scoped event loop and accessed in the next test's loop.
    close_pool() is handled separately in _db_setup (only for DB tests).
    """
    yield
    await close_redis()
    await close_arq()


@pytest.fixture
async def _db_setup() -> AsyncIterator[None]:
    """Apply migrations (idempotent), then clean up data after the test.

    Function-scoped so each test gets a fresh pool in its own event loop —
    avoids asyncpg "attached to a different loop" errors.
    """
    await run_migrations()
    yield
    pool = await get_pool()
    await pool.execute(
        f"TRUNCATE {_TRUNCATE_TABLES} RESTART IDENTITY CASCADE"
    )
    await close_pool()
    await close_redis()


@pytest.fixture
async def db(_db_setup: None) -> AsyncIterator[asyncpg.Connection]:  # type: ignore[type-arg]
    """Per-test DB connection. Data cleanup happens in _db_setup teardown."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        yield conn  # type: ignore[misc]


@pytest.fixture
async def client(_db_setup: None) -> AsyncIterator[AsyncClient]:
    """HTTP client for testing API endpoints."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


from app.api.webhooks import _provider_dep  # noqa: E402
from tests.fakes.fake_provider import FakeProvider  # noqa: E402


@pytest.fixture
async def fake_provider(client: AsyncClient) -> AsyncIterator[FakeProvider]:
    """Provide a FakeProvider and inject it into the FastAPI app for the test."""
    fake = FakeProvider()
    app.dependency_overrides[_provider_dep] = lambda: fake
    try:
        yield fake
    finally:
        app.dependency_overrides.clear()
        fake.reset()
