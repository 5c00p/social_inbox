# Task 03: DB schema + migrations

> Применить в `D:\Work\social_inbox` после успешного завершения Task 01. Положить файл в корень проекта, открыть в VS Code, попросить Claude Code: «Прочитай TASK_03_db_schema.md и выполни все подзадачи последовательно. Перед каждой подзадачей кратко опиши план, после — что фактически сделано».

---

## Контекст

После Task 01 у нас есть рабочий каркас FastAPI + Postgres + Redis в Docker. Postgres-контейнер запущен, но БД пустая.

Задача — наполнить БД схемой данных проекта `social_inbox`, реализовать механизм автоприменения миграций при старте сервиса (паттерн взят из `bot_purify`), и создать первичный data access layer (asyncpg pool + базовые репозитории).

После применения этой задачи у нас есть рабочая БД со всеми таблицами + репозитории для users/conversations/messages. Этого достаточно, чтобы в Task 04–06 начать наполнять БД через webhook-обработчик.

**Важно:** в этой задаче НЕТ бизнес-логики — только схема и data layer. Логика сценариев — в Task 07.

---

## Цель

После выполнения этой задачи:

- В `migrations/` есть 4 SQL-файла с полной схемой БД проекта
- При старте контейнера API миграции применяются автоматически (идемпотентно)
- В `app/repos/` есть `pool.py` + 3 репозитория (users, conversations, messages) с базовыми операциями
- `make test` проходит — есть smoke-тесты, проверяющие создание/чтение записей через репозитории
- Тесты используют **транзакционный rollback** — каждый тест в своей транзакции, после теста rollback
- `/ready` endpoint теперь реально проверяет коннект к Postgres (вместо заглушки из Task 01)

---

## Подзадачи

### 1. Миграции — SQL файлы

a) Создать `migrations/001_users_conversations.sql`:

```sql
-- Migration 001: Core entities — social_users and conversations.
-- Implements §8.1, §8.2 of CLAUDE.md.

-- A user on a social platform. One physical person on one platform = one row.
-- (Same physical person on Instagram and Facebook = two rows.)
CREATE TABLE IF NOT EXISTS social_users (
    id                  BIGSERIAL PRIMARY KEY,
    platform            TEXT NOT NULL CHECK (platform IN ('instagram', 'facebook')),
    external_id         TEXT NOT NULL,
    provider_name       TEXT NOT NULL CHECK (provider_name IN ('sendpulse', 'manychat', 'meta')),
    username            TEXT,
    full_name           TEXT,
    profile_pic_url     TEXT,
    short_id            TEXT NOT NULL UNIQUE,
    first_seen_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_message_at     TIMESTAMPTZ,
    smart_mode_enabled  BOOLEAN NOT NULL DEFAULT TRUE,
    tg_handover_at      TIMESTAMPTZ,
    tg_user_id          BIGINT,
    deleted_at          TIMESTAMPTZ,
    metadata            JSONB NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (provider_name, platform, external_id)
);

CREATE INDEX IF NOT EXISTS idx_social_users_short_id
    ON social_users(short_id);

CREATE INDEX IF NOT EXISTS idx_social_users_last_message
    ON social_users(last_message_at DESC) WHERE deleted_at IS NULL;

-- A logical conversation. One per (user, platform). Can be closed and reopened.
CREATE TABLE IF NOT EXISTS conversations (
    id                  BIGSERIAL PRIMARY KEY,
    user_id             BIGINT NOT NULL REFERENCES social_users(id) ON DELETE CASCADE,
    platform            TEXT NOT NULL CHECK (platform IN ('instagram', 'facebook')),
    status              TEXT NOT NULL DEFAULT 'active'
                        CHECK (status IN ('active', 'closed', 'handover_pending', 'handover_done')),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_message_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    closed_at           TIMESTAMPTZ,
    handover_reason     TEXT
);

CREATE INDEX IF NOT EXISTS idx_conversations_user
    ON conversations(user_id);

CREATE INDEX IF NOT EXISTS idx_conversations_status
    ON conversations(status) WHERE status != 'closed';
```

b) Создать `migrations/002_messages_events.sql`:

```sql
-- Migration 002: Message log and raw event log.
-- Implements §8.3, §8.7 of CLAUDE.md.

-- Forward declaration for scenarios FK
-- (scenarios table is created in 003; we use deferred FK there)

CREATE TABLE IF NOT EXISTS messages (
    id                  BIGSERIAL PRIMARY KEY,
    conversation_id     BIGINT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    direction           TEXT NOT NULL CHECK (direction IN ('in', 'out')),
    text                TEXT,
    media_url           TEXT,
    media_type          TEXT,
    source              TEXT,                    -- dm, comment, comment_private_reply
    scenario_id         BIGINT,                  -- FK added in migration 003
    claude_used         BOOLEAN NOT NULL DEFAULT FALSE,
    claude_model        TEXT,
    claude_tokens_in    INTEGER,
    claude_tokens_out   INTEGER,
    safety_blocked      BOOLEAN NOT NULL DEFAULT FALSE,
    safety_reason       TEXT,
    external_message_id TEXT UNIQUE,             -- platform message id, idempotency key
    raw_payload         JSONB,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_messages_conversation
    ON messages(conversation_id, created_at);

CREATE INDEX IF NOT EXISTS idx_messages_created
    ON messages(created_at DESC);

-- Raw webhook payloads — for debugging and audit.
-- Retention: 30 days (cron in Task 14 will purge).
CREATE TABLE IF NOT EXISTS events_log (
    id                  BIGSERIAL PRIMARY KEY,
    provider_name       TEXT NOT NULL,
    platform            TEXT,
    event_type          TEXT NOT NULL,
    external_event_id   TEXT,
    payload             JSONB NOT NULL,
    signature_valid     BOOLEAN NOT NULL,
    processed_at        TIMESTAMPTZ,
    error               TEXT,
    received_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_events_received
    ON events_log(received_at DESC);

CREATE INDEX IF NOT EXISTS idx_events_unprocessed
    ON events_log(received_at) WHERE processed_at IS NULL;

-- Idempotency: same external_event_id from same provider should not be reprocessed.
CREATE UNIQUE INDEX IF NOT EXISTS uq_events_provider_external
    ON events_log(provider_name, external_event_id)
    WHERE external_event_id IS NOT NULL;
```

c) Создать `migrations/003_scenarios_keywords.sql`:

```sql
-- Migration 003: Scenario templates, keyword triggers, comment-post triggers.
-- Implements §8.4, §8.5, §8.6 of CLAUDE.md.

CREATE TABLE IF NOT EXISTS scenarios (
    id                      BIGSERIAL PRIMARY KEY,
    name                    TEXT NOT NULL UNIQUE,
    type                    TEXT NOT NULL
                            CHECK (type IN ('welcome', 'comment_to_dm', 'faq', 'handover', 'smart')),
    template                TEXT,
    quick_replies           JSONB,                -- [{title, payload}, ...]
    claude_system_prompt    TEXT,
    claude_model            TEXT DEFAULT 'claude-sonnet-4-6',
    next_scenario_id        BIGINT REFERENCES scenarios(id),
    active                  BOOLEAN NOT NULL DEFAULT TRUE,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Add deferred FK from messages.scenario_id (declared in 002).
ALTER TABLE messages
    ADD CONSTRAINT fk_messages_scenario
    FOREIGN KEY (scenario_id) REFERENCES scenarios(id) ON DELETE SET NULL;

CREATE TABLE IF NOT EXISTS keywords (
    id                  BIGSERIAL PRIMARY KEY,
    keyword             TEXT NOT NULL,
    match_type          TEXT NOT NULL CHECK (match_type IN ('exact', 'contains', 'regex')),
    context             TEXT NOT NULL CHECK (context IN ('dm', 'comment', 'both')),
    scenario_id         BIGINT NOT NULL REFERENCES scenarios(id) ON DELETE CASCADE,
    priority            INTEGER NOT NULL DEFAULT 100,
    case_sensitive      BOOLEAN NOT NULL DEFAULT FALSE,
    active              BOOLEAN NOT NULL DEFAULT TRUE,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_keywords_active
    ON keywords(priority, id) WHERE active = TRUE;

CREATE TABLE IF NOT EXISTS comment_triggers (
    id                  BIGSERIAL PRIMARY KEY,
    platform            TEXT NOT NULL CHECK (platform IN ('instagram', 'facebook')),
    post_id             TEXT NOT NULL,
    keyword             TEXT NOT NULL,
    scenario_id         BIGINT NOT NULL REFERENCES scenarios(id) ON DELETE CASCADE,
    active              BOOLEAN NOT NULL DEFAULT TRUE,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (platform, post_id, keyword)
);
```

d) Создать `migrations/004_dedup.sql`:

```sql
-- Migration 004: Deduplication for comment-to-DM (one reply per comment).
-- Implements §8.8 of CLAUDE.md.

CREATE TABLE IF NOT EXISTS comment_replies_dedup (
    comment_id          TEXT PRIMARY KEY,
    replied_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Auto-cleanup: keep dedup entries for 90 days.
-- Cron job in Task 14 will purge older entries; for now just an index hint.
CREATE INDEX IF NOT EXISTS idx_dedup_replied_at
    ON comment_replies_dedup(replied_at);
```

### 2. Connection pool и runner миграций

a) Создать `app/repos/pool.py`:

```python
"""Postgres connection pool and migration runner.

Pattern: same as bot_purify — raw SQL files in migrations/ are applied
in lexicographic order at startup. Each file is wrapped in a transaction.

Tracking applied migrations: a `_migrations` table records each filename
that was applied successfully. Re-running is idempotent.
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import asyncpg

from app.config import get_settings
from app.utils.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

log = get_logger(__name__)

_pool: asyncpg.Pool | None = None

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"


async def get_pool() -> asyncpg.Pool:
    """Return the global pool, creating it on first call."""
    global _pool
    if _pool is None:
        settings = get_settings()
        _pool = await asyncpg.create_pool(
            dsn=settings.postgres_dsn,
            min_size=2,
            max_size=10,
            command_timeout=30,
        )
        log.info("postgres_pool_created", min_size=2, max_size=10)
    return _pool


async def close_pool() -> None:
    """Close the pool. Call on shutdown."""
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
        log.info("postgres_pool_closed")


async def ping() -> bool:
    """Return True if Postgres is reachable. Used by /ready endpoint."""
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            value = await conn.fetchval("SELECT 1")
            return value == 1
    except (asyncpg.PostgresError, OSError) as exc:
        log.warning("postgres_ping_failed", error=str(exc))
        return False


async def run_migrations() -> None:
    """Apply all migration files from migrations/ that have not yet been applied.

    Idempotent: running it twice on the same DB is a no-op.
    Each migration file runs inside a single transaction.
    Errors abort the migration and re-raise.
    """
    pool = await get_pool()

    async with pool.acquire() as conn:
        # Bootstrap migration tracking table.
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS _migrations (
                filename    TEXT PRIMARY KEY,
                applied_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )

        applied: set[str] = {
            r["filename"]
            for r in await conn.fetch("SELECT filename FROM _migrations")
        }

    files = sorted(MIGRATIONS_DIR.glob("*.sql"))
    if not files:
        log.warning("no_migration_files_found", path=str(MIGRATIONS_DIR))
        return

    for file in files:
        if file.name in applied:
            log.debug("migration_skipped", filename=file.name)
            continue

        sql = file.read_text(encoding="utf-8")
        log.info("migration_applying", filename=file.name, size_bytes=len(sql))

        async with pool.acquire() as conn, conn.transaction():
            await conn.execute(sql)
            await conn.execute(
                "INSERT INTO _migrations (filename) VALUES ($1)",
                file.name,
            )

        log.info("migration_applied", filename=file.name)
```

b) Обновить `app/main.py` — вызывать `run_migrations()` на старте и `close_pool()` на shutdown:

```python
"""FastAPI application entry point."""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI

from app.api import health
from app.config import get_settings
from app.repos.pool import close_pool, run_migrations
from app.utils.logging import get_logger, setup_logging


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    setup_logging()
    log = get_logger(__name__)
    settings = get_settings()
    log.info("startup", env=settings.env, provider=settings.messaging_provider)

    await run_migrations()

    yield

    await close_pool()
    log.info("shutdown")


def create_app() -> FastAPI:
    app = FastAPI(
        title="social_inbox",
        version="0.1.0",
        description="Automated messaging service for Instagram/Facebook lead capture",
        lifespan=lifespan,
    )
    app.include_router(health.router)
    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
```

c) Обновить `app/api/health.py` — `/ready` теперь реально проверяет Postgres:

```python
"""Health and readiness endpoints."""
from __future__ import annotations

from fastapi import APIRouter, status
from fastapi.responses import JSONResponse

from app.repos.pool import ping as pg_ping

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict[str, str]:
    """Liveness probe — returns ok if process is up."""
    return {"status": "ok"}


@router.get("/ready")
async def ready() -> JSONResponse:
    """Readiness probe — checks Postgres connectivity.

    Returns 200 + {"status": "ready"} when DB is reachable.
    Returns 503 + {"status": "not_ready", "postgres": "down"} otherwise.
    """
    pg_ok = await pg_ping()
    if pg_ok:
        return JSONResponse(content={"status": "ready", "postgres": "up"})
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content={"status": "not_ready", "postgres": "down"},
    )
```

### 3. Утилита генерации short_id

a) Создать `app/utils/short_id.py`:

```python
"""Generate short, URL-safe IDs for use in Telegram deep links.

Format: 8 chars from alphabet [0-9A-Za-z] (no `_` or `-`).
- `_` is the separator in our deep link format `ig_<short_id>_<scenario>`,
  so it MUST NOT appear inside short_id itself.
- `-` looks ugly in URLs.
At 8 chars, alphabet size 62: 218 trillion combinations,
collisions negligible at our scale (≤100k leads in 5-year horizon).
"""
from __future__ import annotations

from nanoid import generate

ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
LENGTH = 8


def make_short_id() -> str:
    """Return a fresh short_id. Always 8 chars, alphanumeric, no separators."""
    return generate(ALPHABET, LENGTH)
```

### 4. Базовый репозиторий social_users

a) Создать `app/repos/users.py`:

```python
"""Repository for social_users table.

Style: raw SQL via asyncpg (matches bot_purify), no ORM.
All queries respect soft-delete (filter deleted_at IS NULL by default).
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

import asyncpg

from app.models.enums import Platform, ProviderName
from app.repos.pool import get_pool
from app.utils.short_id import make_short_id


async def get_by_external(
    provider_name: ProviderName,
    platform: Platform,
    external_id: str,
) -> asyncpg.Record | None:
    """Return user by (provider, platform, external_id) or None."""
    pool = await get_pool()
    return await pool.fetchrow(
        """
        SELECT * FROM social_users
        WHERE provider_name = $1
          AND platform = $2
          AND external_id = $3
          AND deleted_at IS NULL
        """,
        provider_name, platform, external_id,
    )


async def get_by_short_id(short_id: str) -> asyncpg.Record | None:
    """Return user by short_id or None. Used by /api/lead/{short_id}."""
    pool = await get_pool()
    return await pool.fetchrow(
        """
        SELECT * FROM social_users
        WHERE short_id = $1
          AND deleted_at IS NULL
        """,
        short_id,
    )


async def create(
    *,
    provider_name: ProviderName,
    platform: Platform,
    external_id: str,
    username: str | None = None,
    full_name: str | None = None,
    profile_pic_url: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> asyncpg.Record:
    """Create a new user. Generates short_id internally.

    Caller MUST check via get_by_external() first to avoid duplicates;
    this method does NOT do upsert. (Race conditions are acceptable here —
    UNIQUE (provider_name, platform, external_id) will protect us with IntegrityError.)
    """
    pool = await get_pool()
    short_id = make_short_id()
    return await pool.fetchrow(
        """
        INSERT INTO social_users (
            provider_name, platform, external_id,
            username, full_name, profile_pic_url,
            short_id, metadata
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
        RETURNING *
        """,
        provider_name, platform, external_id,
        username, full_name, profile_pic_url,
        short_id, metadata or {},
    )


async def update_last_message_at(user_id: int, ts: datetime) -> None:
    """Bump last_message_at. Called on every incoming message."""
    pool = await get_pool()
    await pool.execute(
        "UPDATE social_users SET last_message_at = $2 WHERE id = $1",
        user_id, ts,
    )


async def mark_handover(user_id: int, tg_user_id: int, ts: datetime) -> None:
    """Record successful handover to Telegram bot."""
    pool = await get_pool()
    await pool.execute(
        """
        UPDATE social_users
        SET tg_handover_at = $2, tg_user_id = $3
        WHERE id = $1
        """,
        user_id, ts, tg_user_id,
    )


async def soft_delete(user_id: int, ts: datetime) -> None:
    """Mark user as deleted. Physical deletion happens after 30 days (Task 14)."""
    pool = await get_pool()
    await pool.execute(
        "UPDATE social_users SET deleted_at = $2 WHERE id = $1",
        user_id, ts,
    )
```

### 5. Репозиторий conversations

a) Создать `app/repos/conversations.py`:

```python
"""Repository for conversations table."""
from __future__ import annotations

from datetime import datetime

import asyncpg

from app.models.enums import ConversationStatus, Platform
from app.repos.pool import get_pool


async def get_active(user_id: int, platform: Platform) -> asyncpg.Record | None:
    """Return active conversation for (user, platform) or None."""
    pool = await get_pool()
    return await pool.fetchrow(
        """
        SELECT * FROM conversations
        WHERE user_id = $1 AND platform = $2 AND status = 'active'
        ORDER BY created_at DESC
        LIMIT 1
        """,
        user_id, platform,
    )


async def create(user_id: int, platform: Platform) -> asyncpg.Record:
    """Create a new active conversation."""
    pool = await get_pool()
    return await pool.fetchrow(
        """
        INSERT INTO conversations (user_id, platform, status)
        VALUES ($1, $2, 'active')
        RETURNING *
        """,
        user_id, platform,
    )


async def get_or_create(user_id: int, platform: Platform) -> asyncpg.Record:
    """Return active conversation or create new one. Race-safe."""
    existing = await get_active(user_id, platform)
    if existing:
        return existing
    try:
        return await create(user_id, platform)
    except asyncpg.UniqueViolationError:
        # Concurrent creation — fetch the one that won.
        result = await get_active(user_id, platform)
        if result is None:
            raise  # should not happen
        return result


async def update_last_message_at(conversation_id: int, ts: datetime) -> None:
    """Bump last_message_at. Called on every message in this conversation."""
    pool = await get_pool()
    await pool.execute(
        "UPDATE conversations SET last_message_at = $2 WHERE id = $1",
        conversation_id, ts,
    )


async def set_status(
    conversation_id: int,
    status: ConversationStatus,
    reason: str | None = None,
) -> None:
    """Change status. If 'closed' — also set closed_at."""
    pool = await get_pool()
    if status == "closed":
        await pool.execute(
            """
            UPDATE conversations
            SET status = $2, closed_at = NOW(), handover_reason = $3
            WHERE id = $1
            """,
            conversation_id, status, reason,
        )
    else:
        await pool.execute(
            """
            UPDATE conversations
            SET status = $2, handover_reason = COALESCE($3, handover_reason)
            WHERE id = $1
            """,
            conversation_id, status, reason,
        )
```

### 6. Репозиторий messages

a) Создать `app/repos/messages.py`:

```python
"""Repository for messages table."""
from __future__ import annotations

from typing import Any

import asyncpg

from app.models.enums import Direction
from app.repos.pool import get_pool


async def insert(
    *,
    conversation_id: int,
    direction: Direction,
    text: str | None,
    media_url: str | None = None,
    media_type: str | None = None,
    source: str | None = None,
    scenario_id: int | None = None,
    claude_used: bool = False,
    claude_model: str | None = None,
    claude_tokens_in: int | None = None,
    claude_tokens_out: int | None = None,
    safety_blocked: bool = False,
    safety_reason: str | None = None,
    external_message_id: str | None = None,
    raw_payload: dict[str, Any] | None = None,
) -> asyncpg.Record | None:
    """Insert a message. Idempotent on external_message_id.

    Returns the inserted row, or None if a row with same external_message_id
    already exists (UNIQUE conflict).
    """
    pool = await get_pool()
    try:
        return await pool.fetchrow(
            """
            INSERT INTO messages (
                conversation_id, direction, text, media_url, media_type,
                source, scenario_id, claude_used, claude_model,
                claude_tokens_in, claude_tokens_out,
                safety_blocked, safety_reason,
                external_message_id, raw_payload
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15)
            RETURNING *
            """,
            conversation_id, direction, text, media_url, media_type,
            source, scenario_id, claude_used, claude_model,
            claude_tokens_in, claude_tokens_out,
            safety_blocked, safety_reason,
            external_message_id, raw_payload,
        )
    except asyncpg.UniqueViolationError:
        return None


async def get_recent(conversation_id: int, limit: int = 20) -> list[asyncpg.Record]:
    """Return recent messages in a conversation, oldest first."""
    pool = await get_pool()
    rows = await pool.fetch(
        """
        SELECT * FROM messages
        WHERE conversation_id = $1
        ORDER BY created_at DESC
        LIMIT $2
        """,
        conversation_id, limit,
    )
    return list(reversed(rows))
```

### 7. Тестовая инфраструктура

a) В `docker-compose.yml` НЕ добавлять отдельный `postgres-test` контейнер. Вместо этого: тесты используют ту же Postgres-инстанс, но с отдельной БД `social_inbox_test`. Это создаётся скриптом инициализации Postgres.

   Создать `docker/postgres-init.sh`:

```bash
#!/bin/bash
set -e

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    CREATE DATABASE social_inbox_test;
    GRANT ALL PRIVILEGES ON DATABASE social_inbox_test TO $POSTGRES_USER;
EOSQL
```

   Сделать файл исполняемым и подключить через volume в `docker-compose.yml` — добавить в сервис postgres:

```yaml
  postgres:
    image: postgres:16-alpine
    # ... existing config ...
    volumes:
      - postgres_data:/var/lib/postgresql/data
      - ./docker/postgres-init.sh:/docker-entrypoint-initdb.d/init.sh:ro
```

b) Обновить `tests/conftest.py` — заменить хак с `os.environ` на нормальный паттерн с overriding settings:

```python
"""Pytest fixtures.

Pattern: each test runs in a transaction that's rolled back on teardown.
Tests reuse the same DB connection per session for speed.

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


@pytest.fixture(scope="session")
async def _db_setup() -> AsyncIterator[None]:
    """Apply migrations once per test session."""
    await run_migrations()
    yield
    await close_pool()


@pytest.fixture
async def db(_db_setup: None) -> AsyncIterator[asyncpg.Connection]:
    """Per-test connection wrapped in a transaction that always rolls back.

    Use this fixture in tests that need to query the DB directly.
    """
    pool = await get_pool()
    async with pool.acquire() as conn, conn.transaction() as _tx:
        # The transaction context manager will commit on success;
        # we hijack by raising at the end? No — better: use savepoint approach.
        # Simpler: nested transaction via savepoint, then explicit rollback.
        # asyncpg's `transaction()` doesn't support rollback-only out of the box,
        # so we use the trick of starting an explicit transaction we control.
        await conn.execute("SAVEPOINT test_savepoint")
        try:
            yield conn
        finally:
            await conn.execute("ROLLBACK TO SAVEPOINT test_savepoint")


@pytest.fixture
async def client(_db_setup: None) -> AsyncIterator[AsyncClient]:
    """HTTP client for testing API endpoints."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
```

   **Важное замечание про rollback:** asyncpg-транзакции в строгом режиме коммитят на выходе. Простейший workaround — использовать SAVEPOINT и ROLLBACK TO. В реальной production-системе для тестов с честным rollback используют либо отдельную БД на тест, либо трюк с искусственным `Exception` в конце. Здесь применён savepoint-подход: достаточно для smoke-тестов и не загрязняет БД между тестами.

   **Альтернатива (если будут проблемы):** добавить в `conftest.py` фикстуру `clean_db`, которая в `finally` делает `TRUNCATE social_users, conversations, messages, events_log RESTART IDENTITY CASCADE`. Для тестов это даже честнее. Если савепоинт-подход даст странные эффекты — переключаемся на TRUNCATE.

c) Создать `tests/test_repos_users.py`:

```python
"""Tests for app.repos.users."""
from __future__ import annotations

import pytest

from app.repos import users


@pytest.mark.asyncio
async def test_create_and_get_by_external(db) -> None:
    user = await users.create(
        provider_name="sendpulse",
        platform="instagram",
        external_id="ig_123",
        username="test_user",
        full_name="Test User",
    )
    assert user["id"] is not None
    assert user["short_id"] is not None
    assert len(user["short_id"]) == 8
    assert user["deleted_at"] is None

    fetched = await users.get_by_external("sendpulse", "instagram", "ig_123")
    assert fetched is not None
    assert fetched["id"] == user["id"]


@pytest.mark.asyncio
async def test_get_by_short_id(db) -> None:
    user = await users.create(
        provider_name="sendpulse",
        platform="instagram",
        external_id="ig_short_test",
    )
    fetched = await users.get_by_short_id(user["short_id"])
    assert fetched is not None
    assert fetched["external_id"] == "ig_short_test"


@pytest.mark.asyncio
async def test_short_id_is_unique_across_users(db) -> None:
    u1 = await users.create(
        provider_name="sendpulse", platform="instagram", external_id="a",
    )
    u2 = await users.create(
        provider_name="sendpulse", platform="instagram", external_id="b",
    )
    assert u1["short_id"] != u2["short_id"]


@pytest.mark.asyncio
async def test_get_unknown_user_returns_none(db) -> None:
    result = await users.get_by_external("sendpulse", "instagram", "nonexistent")
    assert result is None


@pytest.mark.asyncio
async def test_soft_delete_hides_user(db) -> None:
    from datetime import UTC, datetime

    user = await users.create(
        provider_name="sendpulse", platform="instagram", external_id="to_delete",
    )
    await users.soft_delete(user["id"], datetime.now(UTC))

    found = await users.get_by_external("sendpulse", "instagram", "to_delete")
    assert found is None  # filtered out by `deleted_at IS NULL`
```

d) Создать `tests/test_repos_conversations.py`:

```python
"""Tests for app.repos.conversations."""
from __future__ import annotations

import pytest

from app.repos import conversations, users


@pytest.mark.asyncio
async def test_get_or_create_returns_same_conversation(db) -> None:
    user = await users.create(
        provider_name="sendpulse", platform="instagram", external_id="conv_user_1",
    )
    c1 = await conversations.get_or_create(user["id"], "instagram")
    c2 = await conversations.get_or_create(user["id"], "instagram")
    assert c1["id"] == c2["id"]
    assert c1["status"] == "active"


@pytest.mark.asyncio
async def test_set_status_handover(db) -> None:
    user = await users.create(
        provider_name="sendpulse", platform="instagram", external_id="conv_user_2",
    )
    conv = await conversations.create(user["id"], "instagram")
    await conversations.set_status(conv["id"], "handover_pending", reason="medical_question")

    pool_conn = db
    row = await pool_conn.fetchrow("SELECT * FROM conversations WHERE id = $1", conv["id"])
    assert row["status"] == "handover_pending"
    assert row["handover_reason"] == "medical_question"
```

e) Создать `tests/test_repos_messages.py`:

```python
"""Tests for app.repos.messages."""
from __future__ import annotations

import pytest

from app.repos import conversations, messages, users


@pytest.mark.asyncio
async def test_insert_and_get_recent(db) -> None:
    user = await users.create(
        provider_name="sendpulse", platform="instagram", external_id="msg_user",
    )
    conv = await conversations.create(user["id"], "instagram")

    m1 = await messages.insert(
        conversation_id=conv["id"], direction="in",
        text="Привет!", external_message_id="ext_1",
    )
    m2 = await messages.insert(
        conversation_id=conv["id"], direction="out",
        text="Здравствуй!", external_message_id="ext_2",
    )
    assert m1 is not None
    assert m2 is not None

    recent = await messages.get_recent(conv["id"])
    assert len(recent) == 2
    assert recent[0]["text"] == "Привет!"  # oldest first


@pytest.mark.asyncio
async def test_insert_idempotent_on_external_id(db) -> None:
    user = await users.create(
        provider_name="sendpulse", platform="instagram", external_id="dup_user",
    )
    conv = await conversations.create(user["id"], "instagram")

    m1 = await messages.insert(
        conversation_id=conv["id"], direction="in",
        text="Original", external_message_id="dup_1",
    )
    m2 = await messages.insert(
        conversation_id=conv["id"], direction="in",
        text="Duplicate attempt", external_message_id="dup_1",
    )
    assert m1 is not None
    assert m2 is None  # silent dedup
```

f) Создать `tests/test_short_id.py`:

```python
"""Tests for app.utils.short_id."""
from __future__ import annotations

from app.utils.short_id import LENGTH, make_short_id


def test_short_id_length() -> None:
    assert len(make_short_id()) == LENGTH


def test_short_id_alphabet() -> None:
    sid = make_short_id()
    assert "_" not in sid
    assert "-" not in sid
    assert all(c.isalnum() for c in sid)


def test_short_ids_are_unique() -> None:
    ids = {make_short_id() for _ in range(1000)}
    assert len(ids) == 1000  # collisions extremely unlikely at 8 chars
```

g) Обновить `tests/test_health.py` — после изменения `/ready`:

```python
"""Smoke tests for health endpoints."""
from __future__ import annotations

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_health_returns_ok(client: AsyncClient) -> None:
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_ready_checks_postgres(client: AsyncClient) -> None:
    response = await client.get("/ready")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["postgres"] == "up"
```

### 8. Документация по миграциям

a) Создать `migrations/README.md`:

```markdown
# Migrations

Raw SQL migrations applied at app startup by `app.repos.pool.run_migrations()`.

## Naming convention

`NNN_<short_description>.sql` where `NNN` is a zero-padded 3-digit number.
Migrations are applied in lexicographic order.

## Rules

- Each file MUST be idempotent (use `IF NOT EXISTS`, `IF EXISTS`).
- Each file is wrapped in a transaction by the runner — no `BEGIN`/`COMMIT` inside.
- Once committed to a deployed environment, a migration MUST NOT be edited;
  create a new one instead.
- A migration that fails will be retried on next startup —
  it must therefore be safe to re-apply partially.

## Current migrations

| File | Description |
|------|-------------|
| 001_users_conversations.sql | Core: social_users, conversations |
| 002_messages_events.sql     | Message log + raw webhook event log |
| 003_scenarios_keywords.sql  | Scenario templates, keyword triggers, comment triggers |
| 004_dedup.sql               | Deduplication for comment-to-DM |

## Manual operations

To inspect applied migrations:

```sql
SELECT * FROM _migrations ORDER BY applied_at;
```

To force re-run a migration (DESTRUCTIVE — only on dev):

```sql
DELETE FROM _migrations WHERE filename = '003_scenarios_keywords.sql';
-- then drop affected tables manually, then restart app
```
```

---

## Acceptance criteria

После выполнения всех подзадач выполнить и поставить галочки:

- [ ] Существуют все 4 миграции в `migrations/` с правильным содержимым
- [ ] `migrations/README.md` создан
- [ ] `make down && make up` запускает стек, в логах api видно: `migration_applied filename=001_...`, `002_...`, `003_...`, `004_...`
- [ ] При повторном `make down && make up` миграции пропускаются (`migration_skipped` в DEBUG, или просто отсутствие `migration_applying`)
- [ ] `docker compose exec postgres psql -U social_inbox -d social_inbox -c "\dt"` показывает все 7 таблиц: `social_users`, `conversations`, `messages`, `events_log`, `scenarios`, `keywords`, `comment_triggers`, `comment_replies_dedup`, `_migrations`
- [ ] `docker compose exec postgres psql -U social_inbox -d social_inbox_test -c "\dt"` тоже показывает таблицы (после первого запуска тестов)
- [ ] `curl http://localhost:8000/ready` возвращает `{"status":"ready","postgres":"up"}`
- [ ] Если остановить postgres-контейнер: `curl http://localhost:8000/ready` возвращает 503 `{"status":"not_ready","postgres":"down"}`
- [ ] `make test` проходит, все тесты зелёные:
  - `test_health.py` — 2 теста
  - `test_short_id.py` — 3 теста
  - `test_repos_users.py` — 5 тестов
  - `test_repos_conversations.py` — 2 теста
  - `test_repos_messages.py` — 2 теста
- [ ] `make lint` проходит без ошибок
- [ ] После прогона тестов БД `social_inbox_test` чистая — `SELECT count(*) FROM social_users` возвращает 0 (rollback работает)
- [ ] short_id, генерируемый `make_short_id()`, всегда 8 символов из `[0-9A-Za-z]` без `_` и `-`

---

## Do NOT

- НЕ использовать Alembic. Только raw SQL в `migrations/*.sql`.
- НЕ добавлять SQLAlchemy. asyncpg напрямую.
- НЕ редактировать уже применённые миграции (даже на dev). Любое изменение — новая миграция (005, 006, ...).
- НЕ ставить `BEGIN`/`COMMIT` внутри SQL-файлов миграций — раннер сам оборачивает в транзакцию.
- НЕ генерировать short_id средствами Postgres (типа `gen_random_uuid()` или sequences) — короткий ID — наша внешняя гарантия, генерация в Python через nanoid.
- НЕ удалять `_migrations` таблицу или строки из неё в production коде. Только вручную в dev для отладки.
- НЕ возвращать сырые asyncpg.Record из API endpoint'ов — это Task 11 (lead endpoint), там будет сериализация в Pydantic-модели. Сейчас репозитории возвращают Record, и это правильно для Task 03.
- НЕ добавлять методы в репозитории, не указанные в подзадачах. Минимум, необходимый для проверки в тестах. Расширение — в следующих задачах по мере необходимости.
- НЕ писать `metadata=metadata or {}` в SQL-параметрах — asyncpg сериализует словари в JSONB сам, передавать `dict` напрямую корректно.
- НЕ добавлять зависимости вне списка из Task 01.

---

## Зависимости задачи

- Task 01 должна быть применена (есть `app/`, `pyproject.toml`, `docker-compose.yml`)
- При первом запуске `make test` нужно, чтобы БД `social_inbox_test` уже существовала (создаётся через `docker/postgres-init.sh` при первом старте postgres-контейнера). Если контейнер был запущен ДО Task 03, нужно: `make down && docker volume rm social-inbox_postgres_data && make up` чтобы init-скрипт отработал.

---

## Что после этой задачи

После применения Task 03 у нас есть полная схема БД и data layer. Дальше:

- **Task 04** — MessagingProvider interface + IncomingEvent / OutgoingMessage Pydantic-модели
- **Task 05** — SendPulseProvider implementation (требует API credentials от Юли)
- **Task 06** — Webhook endpoint + arq worker scaffold

---

**Дата создания:** 2026-04-30
**Применять в:** `D:\Work\social_inbox` после Task 01
**Эстимейт:** 3–4 часа на Claude Code + ручная проверка
