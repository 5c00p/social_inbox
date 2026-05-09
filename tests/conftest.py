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
from app.providers import reset_provider, set_provider  # noqa: E402
from app.repos.pool import close_pool, get_pool, run_migrations  # noqa: E402
from app.repos.redis_client import close_redis  # noqa: E402
from app.workers.enqueue import close_arq  # noqa: E402

# Tables to truncate between tests (CASCADE handles dependent tables).
_TRUNCATE_TABLES = "social_users, scenarios, events_log, comment_replies_dedup"


@pytest.fixture(autouse=True)
async def _cleanup_singletons() -> AsyncIterator[None]:
    """Close all singletons after every test.

    Prevents "Event loop is closed" errors when a singleton is created in one
    test's function-scoped event loop and accessed in the next test's loop.
    Also covers the case where _db_setup setup fails (exception before yield
    means its own teardown code never runs).
    """
    yield
    await close_pool()
    await close_redis()
    await close_arq()
    reset_provider()
    get_settings.cache_clear()


@pytest.fixture
async def _db_setup() -> AsyncIterator[None]:
    """Apply migrations (idempotent), then clean up data after the test.

    Function-scoped so each test gets a fresh pool in its own event loop —
    avoids asyncpg "attached to a different loop" errors.
    """
    await run_migrations()
    # Re-seed reference scenarios after each TRUNCATE — migrations run once,
    # but TRUNCATE in teardown wipes all scenario rows.
    pool = await get_pool()
    await pool.execute(
        "INSERT INTO scenarios (name, type, template, active)"
        " VALUES ('echo_scenario', 'echo', 'Received: {text}', TRUE)"
        " ON CONFLICT (name) DO NOTHING"
    )
    await pool.execute(
        """
        INSERT INTO scenarios (name, type, template, metadata, active)
        VALUES ('default_welcome', 'welcome', $1, $2, TRUE)
        ON CONFLICT (name) DO NOTHING
        """,
        "🌿 Привет, {first_name}!\n\n{tg_link}\n\n{disclaimer}",
        {
            "tg_scenario_slug": "purify",
            "quick_replies": [
                {"title": "Перейти в Telegram", "type": "url", "payload": "{tg_link}"},
                {"title": "Узнать больше", "type": "postback", "payload": "more_info"},
            ],
        },
    )
    await pool.execute(
        """
        INSERT INTO scenarios (name, type, template, metadata, active)
        VALUES ('default_purify_comment', 'comment_to_dm', $1, $2, TRUE)
        ON CONFLICT (name) DO NOTHING
        """,
        "🌿 Привет, {first_name}! {tg_link}\n\n{disclaimer}",
        {
            "tg_scenario_slug": "purify",
            "public_reply_text": "Отправила в личку 💌",
            "quick_replies": [{"title": "Перейти в Telegram", "type": "url", "payload": "{tg_link}"}],
        },
    )
    # Re-seed global keyword "очищение" → default_purify_comment
    await pool.execute(
        """
        INSERT INTO keywords (keyword, match_type, context, scenario_id, priority, case_sensitive, active)
        SELECT 'очищение', 'contains', 'comment', s.id, 50, FALSE, TRUE
        FROM scenarios s
        WHERE s.name = 'default_purify_comment'
        ON CONFLICT DO NOTHING
        """
    )
    await pool.execute(
        """
        INSERT INTO scenarios (name, type, template, metadata, active)
        VALUES ('default_smart', 'smart', NULL, '{"claude_model": null}'::jsonb, TRUE)
        ON CONFLICT (name) DO NOTHING
        """
    )
    yield
    pool = await get_pool()
    await pool.execute(
        f"TRUNCATE {_TRUNCATE_TABLES} RESTART IDENTITY CASCADE"
    )
    # Flush Redis DB to prevent key collisions between tests.
    # Tests use DB index 1 (separate from prod DB 0). TRUNCATE resets PK sequences
    # to 1, so user_id=1 in test A and test B are different users sharing same Redis keys.
    from app.repos.redis_client import get_redis
    redis = await get_redis()
    await redis.flushdb()
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
    """Inject FakeProvider into both FastAPI routes and the provider singleton.

    FastAPI dependency override covers webhook endpoints.
    set_provider covers workers that call get_provider() directly.
    """
    fake = FakeProvider()
    app.dependency_overrides[_provider_dep] = lambda: fake
    set_provider(fake)
    try:
        yield fake
    finally:
        app.dependency_overrides.clear()
        fake.reset()
