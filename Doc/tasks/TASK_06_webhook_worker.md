# Task 06: Webhook endpoint + arq worker scaffold

> Применить в `D:\Work\social_inbox` после успешного завершения Tasks 01, 03, 04. Положить файл в корень проекта, открыть в VS Code, попросить Claude Code: «Прочитай TASK_06_webhook_worker.md и выполни все подзадачи последовательно. Перед каждой подзадачей кратко опиши план, после — что фактически сделано».

---

## Контекст

После Tasks 01–04 у нас есть:

- БД со схемой и репозиториями (Task 03)
- Абстракция MessagingProvider, фабрика, FakeProvider для тестов (Task 04)

Чего НЕ хватает: **реальный поток данных**. Webhook'и от провайдера никуда не приходят, очередь arq пустая, worker в docker-compose стоит как `sleep infinity`.

Задача — построить **транспорт**:

1. **Webhook endpoint** `POST /webhooks/{provider_name}` — принимает payload, валидирует через провайдер, записывает в `events_log`, кладёт `IncomingEvent` в Redis-очередь arq, возвращает 200 OK
2. **arq worker** — берёт события из очереди, находит/создаёт `social_user`, `conversation`, пишет входящее сообщение в `messages`, отмечает событие как обработанное в `events_log`
3. **Heartbeat** — worker раз в минуту обновляет ключ `worker:heartbeat` в Redis (база для healthcheck в Task 16)

**В этой задаче нет реакции на события.** Никаких автоответов, никакого Claude, никакого запуска сценариев. Это будет в Task 07. Сейчас webhook → БД, и всё.

После применения этой задачи можно посылать кастомные события через FakeProvider в тестах и видеть полный путь данных от webhook до записи в БД.

---

## Цель

После выполнения этой задачи:

- `POST /webhooks/sendpulse` принимает любой JSON-payload, валидирует подпись через `SendPulseProvider.parse_webhook` (сейчас вернёт `NotImplementedError` — это ОК для этой задачи: тесты идут через FakeProvider)
- Все входящие события логируются в таблицу `events_log` с полным сырым payload
- arq worker запущен в docker-compose, видит задачи в Redis, выполняет
- Воркер создаёт `social_users`, `conversations`, `messages` для каждого входящего события
- Idempotency: повторная обработка того же `external_event_id` — no-op
- `make test` проходит, новые тесты используют FakeProvider через FastAPI dependency override
- `/ready` теперь проверяет ещё и Redis (в дополнение к Postgres из Task 03)

---

## Подзадачи

### 1. Redis pool

a) Создать `app/repos/redis_client.py`:

```python
"""Singleton Redis client.

Used by:
- arq queue (job enqueue/dequeue)
- worker heartbeat
- rate limiter (Task 14)
- token cache for SendPulse OAuth (Task 05)
"""
from __future__ import annotations

from redis.asyncio import Redis, from_url

from app.config import get_settings
from app.utils.logging import get_logger

log = get_logger(__name__)

_client: Redis | None = None


async def get_redis() -> Redis:
    """Return the global Redis client, creating it on first call."""
    global _client
    if _client is None:
        settings = get_settings()
        _client = from_url(
            settings.redis_url,
            encoding="utf-8",
            decode_responses=True,
            health_check_interval=30,
        )
        log.info("redis_client_created")
    return _client


async def close_redis() -> None:
    """Close the Redis client. Call on shutdown."""
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None
        log.info("redis_client_closed")


async def ping() -> bool:
    """Return True if Redis is reachable. Used by /ready endpoint."""
    try:
        client = await get_redis()
        return bool(await client.ping())
    except (ConnectionError, OSError) as exc:
        log.warning("redis_ping_failed", error=str(exc))
        return False
```

b) Обновить `app/main.py` — закрывать Redis на shutdown (рядом с `close_pool`):

```python
@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    setup_logging()
    log = get_logger(__name__)
    settings = get_settings()
    log.info("startup", env=settings.env, provider=settings.messaging_provider)

    await run_migrations()

    yield

    await close_pool()
    await close_redis()
    log.info("shutdown")
```

   Добавить импорт: `from app.repos.redis_client import close_redis`.

c) Обновить `app/api/health.py` — `/ready` проверяет и Postgres, и Redis:

```python
"""Health and readiness endpoints."""
from __future__ import annotations

from fastapi import APIRouter, status
from fastapi.responses import JSONResponse

from app.repos.pool import ping as pg_ping
from app.repos.redis_client import ping as redis_ping

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict[str, str]:
    """Liveness probe — returns ok if process is up."""
    return {"status": "ok"}


@router.get("/ready")
async def ready() -> JSONResponse:
    """Readiness probe — checks Postgres and Redis."""
    pg_ok = await pg_ping()
    redis_ok = await redis_ping()
    body = {
        "status": "ready" if (pg_ok and redis_ok) else "not_ready",
        "postgres": "up" if pg_ok else "down",
        "redis": "up" if redis_ok else "down",
    }
    code = status.HTTP_200_OK if (pg_ok and redis_ok) else status.HTTP_503_SERVICE_UNAVAILABLE
    return JSONResponse(status_code=code, content=body)
```

### 2. Репозиторий events_log

a) Создать `app/repos/events.py`:

```python
"""Repository for events_log table."""
from __future__ import annotations

from datetime import datetime
from typing import Any

import asyncpg

from app.repos.pool import get_pool


async def insert(
    *,
    provider_name: str,
    platform: str | None,
    event_type: str,
    external_event_id: str | None,
    payload: dict[str, Any],
    signature_valid: bool,
) -> asyncpg.Record:
    """Insert a raw webhook event. Returns the inserted row.

    Raises asyncpg.UniqueViolationError if (provider_name, external_event_id)
    already exists — caller should treat this as duplicate and skip processing.
    """
    pool = await get_pool()
    return await pool.fetchrow(
        """
        INSERT INTO events_log (
            provider_name, platform, event_type,
            external_event_id, payload, signature_valid
        )
        VALUES ($1, $2, $3, $4, $5, $6)
        RETURNING *
        """,
        provider_name, platform, event_type,
        external_event_id, payload, signature_valid,
    )


async def mark_processed(event_id: int, error: str | None = None) -> None:
    """Mark event as processed. If error is provided — also store it."""
    pool = await get_pool()
    await pool.execute(
        """
        UPDATE events_log
        SET processed_at = NOW(), error = $2
        WHERE id = $1
        """,
        event_id, error,
    )


async def is_already_processed(
    provider_name: str,
    external_event_id: str,
) -> bool:
    """Check if this external event has already been processed (processed_at IS NOT NULL)."""
    pool = await get_pool()
    row = await pool.fetchrow(
        """
        SELECT processed_at FROM events_log
        WHERE provider_name = $1 AND external_event_id = $2
        """,
        provider_name, external_event_id,
    )
    return row is not None and row["processed_at"] is not None
```

### 3. arq settings и enqueue helper

a) Создать `app/workers/arq_settings.py`:

```python
"""arq worker configuration.

Run worker:
    arq app.workers.arq_settings.WorkerSettings

Tasks defined here are auto-registered by arq.
"""
from __future__ import annotations

from typing import Any

from arq.connections import RedisSettings

from app.config import get_settings
from app.workers.tasks_messages import process_incoming_event
from app.workers.heartbeat import heartbeat_tick


def _redis_settings() -> RedisSettings:
    settings = get_settings()
    return RedisSettings.from_dsn(settings.redis_url)


class WorkerSettings:
    """Settings consumed by `arq` CLI runner."""

    redis_settings = _redis_settings()

    # Tasks
    functions: list[Any] = [process_incoming_event]

    # Heartbeat: run every 60 seconds
    cron_jobs: list[Any] = [
        # Note: arq.cron requires explicit import here; configured in Task 16 for richer schedule.
        # For now we register a periodic task via on_startup loop.
    ]

    # Concurrency
    max_jobs = 10
    job_timeout = 60      # seconds — webhook events should be fast
    keep_result = 60      # seconds to keep job result in Redis (for debugging)
    max_tries = 3         # exponential retry on exception

    # Logging
    log_results = True

    @staticmethod
    async def on_startup(ctx: dict[str, Any]) -> None:
        # Schedule heartbeat in background loop
        import asyncio
        ctx["heartbeat_task"] = asyncio.create_task(_heartbeat_loop())

    @staticmethod
    async def on_shutdown(ctx: dict[str, Any]) -> None:
        task = ctx.get("heartbeat_task")
        if task:
            task.cancel()


async def _heartbeat_loop() -> None:
    """Background task: write timestamp to Redis every 60 seconds."""
    import asyncio
    while True:
        try:
            await heartbeat_tick()
        except Exception:
            # Defensive: heartbeat failures must NOT crash the worker.
            pass
        await asyncio.sleep(60)
```

b) Создать `app/workers/heartbeat.py`:

```python
"""Worker liveness heartbeat written to Redis.

Healthchecks (in Task 16) read the key and confirm it's recent.
"""
from __future__ import annotations

from datetime import UTC, datetime

from app.repos.redis_client import get_redis
from app.utils.logging import get_logger

log = get_logger(__name__)

HEARTBEAT_KEY = "worker:heartbeat"
HEARTBEAT_TTL_SECONDS = 180  # if missing for >3 min, worker is unhealthy


async def heartbeat_tick() -> None:
    """Write current UTC timestamp to Redis."""
    redis = await get_redis()
    now = datetime.now(UTC).isoformat()
    await redis.set(HEARTBEAT_KEY, now, ex=HEARTBEAT_TTL_SECONDS)


async def heartbeat_age_seconds() -> float | None:
    """Return seconds since last heartbeat, or None if missing."""
    redis = await get_redis()
    value = await redis.get(HEARTBEAT_KEY)
    if not value:
        return None
    try:
        ts = datetime.fromisoformat(value)
    except ValueError:
        return None
    return (datetime.now(UTC) - ts).total_seconds()
```

c) Создать `app/workers/enqueue.py` — helper для постановки задачи в очередь:

```python
"""Helper to enqueue events from FastAPI handlers into arq queue."""
from __future__ import annotations

from arq import create_pool
from arq.connections import ArqRedis, RedisSettings

from app.config import get_settings
from app.models.events import IncomingEvent
from app.utils.logging import get_logger

log = get_logger(__name__)

_arq: ArqRedis | None = None


async def get_arq() -> ArqRedis:
    """Return arq connection pool, creating on first call."""
    global _arq
    if _arq is None:
        settings = get_settings()
        _arq = await create_pool(RedisSettings.from_dsn(settings.redis_url))
        log.info("arq_pool_created")
    return _arq


async def close_arq() -> None:
    global _arq
    if _arq is not None:
        await _arq.aclose()
        _arq = None


async def enqueue_event(event: IncomingEvent, log_id: int) -> None:
    """Enqueue an IncomingEvent for the worker to process.

    Args:
        event: the parsed event
        log_id: id of the row in events_log (so worker can mark it processed)
    """
    arq = await get_arq()
    await arq.enqueue_job(
        "process_incoming_event",
        event.model_dump(mode="json"),
        log_id,
    )
```

d) Обновить `app/main.py` — закрывать arq pool на shutdown:

```python
# В lifespan():
await close_pool()
await close_redis()
await close_arq()
log.info("shutdown")
```

   И импорт: `from app.workers.enqueue import close_arq`.

### 4. Webhook endpoint

a) Создать `app/api/webhooks.py`:

```python
"""Webhook endpoints — entry point for messaging providers.

Pattern:
1. Read raw body (DO NOT parse before signature check).
2. Pass to provider.parse_webhook() for validation + parsing.
3. Log raw payload to events_log.
4. Enqueue each parsed event for the worker.
5. Always return 200 OK, even on parse failures —
   otherwise the provider may mark our endpoint as broken.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from app.providers import MessagingProvider, get_provider
from app.repos import events as events_repo
from app.utils.logging import get_logger
from app.workers.enqueue import enqueue_event

log = get_logger(__name__)

router = APIRouter(tags=["webhooks"])


def _provider_dep() -> MessagingProvider:
    """FastAPI dependency wrapper around the singleton factory.

    Tests override via app.dependency_overrides[_provider_dep] = lambda: FakeProvider().
    """
    return get_provider()


@router.get("/webhooks/{provider_name}")
async def webhook_verification(provider_name: str, request: Request) -> dict[str, str]:
    """Some providers send a verification GET request when subscribing.

    For SendPulse this is unused, but Meta-style providers send a hub.challenge.
    We respond generically with 'ok' for now; provider-specific handling can be
    added when MetaProvider is implemented.
    """
    log.info("webhook_verification_received", provider=provider_name)
    # If 'hub.challenge' query param is present, echo it back (Meta convention)
    challenge = request.query_params.get("hub.challenge")
    if challenge:
        return {"hub.challenge": challenge}
    return {"status": "ok"}


@router.post("/webhooks/{provider_name}")
async def webhook_receive(
    provider_name: str,
    request: Request,
    provider: MessagingProvider = Depends(_provider_dep),
) -> dict[str, str]:
    """Receive a webhook from a messaging provider.

    Always returns 200 OK to keep the provider from marking us broken.
    """
    raw_body = await request.body()
    headers = dict(request.headers)

    # Sanity: if URL provider name doesn't match configured provider, log and accept.
    if provider_name != provider.name:
        log.warning(
            "webhook_provider_mismatch",
            url_provider=provider_name,
            active_provider=provider.name,
        )

    try:
        events = await provider.parse_webhook(raw_body, headers)
    except Exception as exc:
        # parse_webhook should not raise per contract, but defensive:
        log.exception("webhook_parse_failed", provider=provider_name, error=str(exc))
        events = []

    log.info(
        "webhook_received",
        provider=provider_name,
        events_count=len(events),
        body_size=len(raw_body),
    )

    for event in events:
        try:
            row = await events_repo.insert(
                provider_name=event.provider,
                platform=event.platform,
                event_type=event.event_type,
                external_event_id=event.external_event_id,
                payload=event.raw_payload,
                signature_valid=True,  # if we got here, parse_webhook accepted it
            )
            await enqueue_event(event, row["id"])
        except Exception as exc:
            # asyncpg.UniqueViolationError if duplicate external_event_id —
            # silently ignore; events_log unique index protects us.
            log.warning(
                "webhook_event_persist_failed",
                external_event_id=event.external_event_id,
                error=str(exc),
            )

    return {"status": "ok"}
```

b) Подключить роутер в `app/main.py`:

```python
def create_app() -> FastAPI:
    app = FastAPI(
        title="social_inbox",
        version="0.1.0",
        description="Automated messaging service for Instagram/Facebook lead capture",
        lifespan=lifespan,
    )
    app.include_router(health.router)
    app.include_router(webhooks.router)
    return app
```

   Импорт: `from app.api import health, webhooks`.

### 5. Worker task — process_incoming_event

a) Создать `app/workers/tasks_messages.py`:

```python
"""arq task: process a single IncomingEvent.

This is the SOLE consumer of the events queue.

What it does (in this task):
1. Idempotency check: if events_log.processed_at IS NOT NULL → skip
2. Find or create social_user
3. Find or create active conversation
4. Insert message with direction='in'
5. Bump social_users.last_message_at and conversations.last_message_at
6. Mark events_log row as processed

What it does NOT do (yet):
- Run scenarios (Task 07)
- Generate replies (Task 13 — Claude)
- Apply safety filters (Task 14)
- Send anything outbound

The full pipeline grows from this scaffold in subsequent tasks.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.models.events import IncomingEvent
from app.repos import conversations, events as events_repo, messages, users
from app.utils.logging import get_logger

log = get_logger(__name__)


async def process_incoming_event(
    ctx: dict[str, Any],
    event_dict: dict[str, Any],
    log_id: int,
) -> None:
    """Process a single event from the queue.

    Args:
        ctx: arq job context (unused for now, available for DB pool reuse later)
        event_dict: serialized IncomingEvent (model_dump(mode='json'))
        log_id: id of the events_log row to mark as processed at the end
    """
    event = IncomingEvent.model_validate(event_dict)

    # 1. Idempotency check — was this event_id already processed?
    if await events_repo.is_already_processed(event.provider, event.external_event_id):
        log.info(
            "event_skipped_already_processed",
            external_event_id=event.external_event_id,
            log_id=log_id,
        )
        return

    age_seconds = (datetime.now(UTC) - event.occurred_at).total_seconds()
    log.info(
        "event_processing",
        external_event_id=event.external_event_id,
        platform=event.platform,
        event_type=event.event_type,
        age_seconds=int(age_seconds),
    )

    try:
        # 2. Find or create social_user
        user = await users.get_by_external(
            event.provider, event.platform, event.external_user_id,
        )
        if user is None:
            user = await users.create(
                provider_name=event.provider,
                platform=event.platform,
                external_id=event.external_user_id,
                username=event.username,
                full_name=event.full_name,
            )
            log.info(
                "user_created",
                user_id=user["id"],
                short_id=user["short_id"],
                external_id=event.external_user_id,
            )

        # 3. Find or create active conversation
        conv = await conversations.get_or_create(user["id"], event.platform)

        # 4. Insert message
        msg = await messages.insert(
            conversation_id=conv["id"],
            direction="in",
            text=event.text,
            media_url=event.media_url,
            source=_source_from_event_type(event.event_type),
            external_message_id=event.external_event_id,
            raw_payload=event.raw_payload,
        )
        if msg is None:
            log.info(
                "message_skipped_duplicate",
                external_event_id=event.external_event_id,
            )

        # 5. Bump timestamps
        await users.update_last_message_at(user["id"], event.occurred_at)
        await conversations.update_last_message_at(conv["id"], event.occurred_at)

        # 6. Mark event processed (success)
        await events_repo.mark_processed(log_id, error=None)

        log.info("event_processed_ok", log_id=log_id, user_id=user["id"], conv_id=conv["id"])
    except Exception as exc:
        log.exception("event_processing_failed", log_id=log_id, error=str(exc))
        await events_repo.mark_processed(log_id, error=str(exc)[:500])
        raise  # arq retries with exponential backoff


def _source_from_event_type(event_type: str) -> str:
    """Map EventType to messages.source value."""
    return {
        "message": "dm",
        "comment": "comment",
        "postback": "postback",
    }.get(event_type, "unknown")
```

### 6. Docker compose — поднять worker

a) В `docker-compose.yml` заменить блок `worker`:

```yaml
  worker:
    build:
      context: .
      dockerfile: docker/Dockerfile
    restart: unless-stopped
    env_file: .env
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy
    volumes:
      - ./app:/app/app
      - ./migrations:/app/migrations
    command: ["uv", "run", "arq", "app.workers.arq_settings.WorkerSettings"]
    networks:
      - default
```

b) Worker НЕ применяет миграции — это делает api на старте. Worker полагается на то, что api поднимется первым и применит миграции. Это нормальный паттерн для compose: api запускается как часть `depends_on`, миграции отрабатывают, потом worker подключается к уже готовой БД.

   Если миграции долгие — в будущем стоит вынести их в отдельный init-контейнер. Пока (5 SQL-файлов, <100мс) хватает текущего подхода.

### 7. Тесты

a) Обновить `tests/conftest.py` — добавить фикстуру `fake_provider`:

```python
# Добавить в конец файла conftest.py

from app.api.webhooks import _provider_dep
from tests.fakes.fake_provider import FakeProvider


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
```

   Нужный импорт в начале файла (если ещё нет):

```python
from collections.abc import AsyncIterator
```

b) Создать `tests/test_webhook_endpoint.py`:

```python
"""Tests for /webhooks/{provider} endpoint."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest
from httpx import AsyncClient

from app.models.events import IncomingEvent
from tests.fakes.fake_provider import FakeProvider


def _make_event(
    external_event_id: str = "evt_1",
    external_user_id: str = "user_1",
    text: str = "Hello",
) -> IncomingEvent:
    return IncomingEvent(
        provider="sendpulse",
        platform="instagram",
        event_type="message",
        external_user_id=external_user_id,
        external_event_id=external_event_id,
        username="alice",
        text=text,
        occurred_at=datetime(2026, 4, 30, 12, 0, tzinfo=UTC),
    )


@pytest.mark.asyncio
async def test_webhook_returns_200_on_empty_events(
    client: AsyncClient, fake_provider: FakeProvider, db,
) -> None:
    # No events queued — provider returns []
    response = await client.post("/webhooks/sendpulse", json={})
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_webhook_returns_200_on_invalid_signature(
    client: AsyncClient, fake_provider: FakeProvider, db,
) -> None:
    """Critical contract: NEVER return non-200 from webhook."""
    fake_provider.signature_valid = False
    fake_provider.queue_event(_make_event())

    response = await client.post("/webhooks/sendpulse", json={"foo": "bar"})
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_webhook_records_event_in_log(
    client: AsyncClient, fake_provider: FakeProvider, db,
) -> None:
    event = _make_event(external_event_id="evt_log_1")
    fake_provider.queue_event(event)

    response = await client.post("/webhooks/sendpulse", json={})
    assert response.status_code == 200

    row = await db.fetchrow(
        "SELECT * FROM events_log WHERE external_event_id = $1",
        "evt_log_1",
    )
    assert row is not None
    assert row["provider_name"] == "sendpulse"
    assert row["event_type"] == "message"
    assert row["signature_valid"] is True


@pytest.mark.asyncio
async def test_webhook_duplicate_event_id_does_not_crash(
    client: AsyncClient, fake_provider: FakeProvider, db,
) -> None:
    event = _make_event(external_event_id="evt_dup")
    fake_provider.queue_event(event)
    await client.post("/webhooks/sendpulse", json={})

    # Second time — same event_id, should be silently dropped at events_log unique index
    fake_provider.queue_event(event)
    response = await client.post("/webhooks/sendpulse", json={})
    assert response.status_code == 200

    rows = await db.fetch(
        "SELECT * FROM events_log WHERE external_event_id = $1",
        "evt_dup",
    )
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_webhook_handles_parse_exception(
    client: AsyncClient, fake_provider: FakeProvider, db,
) -> None:
    """If provider.parse_webhook raises, endpoint still returns 200."""

    class BrokenProvider(FakeProvider):
        async def parse_webhook(self, raw_body, headers):
            raise RuntimeError("intentional bug")

    from app.api.webhooks import _provider_dep
    from app.main import app

    broken = BrokenProvider()
    app.dependency_overrides[_provider_dep] = lambda: broken
    try:
        response = await client.post("/webhooks/sendpulse", json={})
        assert response.status_code == 200
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_webhook_get_verification_echoes_challenge(
    client: AsyncClient,
) -> None:
    """Meta-style verification: GET with hub.challenge → echo it back."""
    response = await client.get("/webhooks/sendpulse?hub.challenge=12345")
    assert response.status_code == 200
    assert response.json() == {"hub.challenge": "12345"}


@pytest.mark.asyncio
async def test_webhook_get_without_challenge(client: AsyncClient) -> None:
    response = await client.get("/webhooks/sendpulse")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
```

c) Создать `tests/test_worker_process_event.py`:

```python
"""Tests for worker task: process_incoming_event.

These tests call the task function directly (no arq runtime needed).
"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.models.events import IncomingEvent
from app.repos import conversations, events as events_repo, users
from app.workers.tasks_messages import process_incoming_event


def _event(
    external_event_id: str = "evt_w_1",
    external_user_id: str = "ig_user_1",
    text: str = "Hi",
) -> IncomingEvent:
    return IncomingEvent(
        provider="sendpulse",
        platform="instagram",
        event_type="message",
        external_user_id=external_user_id,
        external_event_id=external_event_id,
        username="bob",
        full_name="Bob Smith",
        text=text,
        occurred_at=datetime(2026, 4, 30, 12, 0, tzinfo=UTC),
    )


@pytest.mark.asyncio
async def test_process_event_creates_user_conversation_message(db) -> None:
    # Setup: insert event_log row first (webhook handler does this in production)
    event = _event(external_event_id="evt_e2e_1")
    log_row = await events_repo.insert(
        provider_name=event.provider,
        platform=event.platform,
        event_type=event.event_type,
        external_event_id=event.external_event_id,
        payload={},
        signature_valid=True,
    )

    # Act
    await process_incoming_event({}, event.model_dump(mode="json"), log_row["id"])

    # Assert: user created
    user = await users.get_by_external("sendpulse", "instagram", "ig_user_1")
    assert user is not None
    assert user["username"] == "bob"

    # Assert: conversation created
    conv = await conversations.get_active(user["id"], "instagram")
    assert conv is not None

    # Assert: message inserted
    row = await db.fetchrow(
        "SELECT * FROM messages WHERE conversation_id = $1",
        conv["id"],
    )
    assert row is not None
    assert row["direction"] == "in"
    assert row["text"] == "Hi"
    assert row["source"] == "dm"

    # Assert: events_log marked processed
    log_after = await db.fetchrow(
        "SELECT processed_at, error FROM events_log WHERE id = $1",
        log_row["id"],
    )
    assert log_after["processed_at"] is not None
    assert log_after["error"] is None


@pytest.mark.asyncio
async def test_process_event_idempotent_via_events_log(db) -> None:
    """Replaying the same event_id is a no-op."""
    event = _event(external_event_id="evt_idem_1")
    log_row = await events_repo.insert(
        provider_name=event.provider,
        platform=event.platform,
        event_type=event.event_type,
        external_event_id=event.external_event_id,
        payload={},
        signature_valid=True,
    )

    # First processing
    await process_incoming_event({}, event.model_dump(mode="json"), log_row["id"])

    # Second processing — should skip
    await process_incoming_event({}, event.model_dump(mode="json"), log_row["id"])

    # Only one message in DB
    user = await users.get_by_external("sendpulse", "instagram", event.external_user_id)
    assert user is not None
    conv = await conversations.get_active(user["id"], "instagram")
    rows = await db.fetch(
        "SELECT * FROM messages WHERE conversation_id = $1",
        conv["id"],
    )
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_process_event_for_existing_user(db) -> None:
    """If user already exists, do not create duplicate."""
    # Pre-create user
    await users.create(
        provider_name="sendpulse",
        platform="instagram",
        external_id="existing_user",
        username="old_username",
    )

    event = _event(
        external_event_id="evt_existing_1",
        external_user_id="existing_user",
    )
    log_row = await events_repo.insert(
        provider_name=event.provider,
        platform=event.platform,
        event_type=event.event_type,
        external_event_id=event.external_event_id,
        payload={},
        signature_valid=True,
    )
    await process_incoming_event({}, event.model_dump(mode="json"), log_row["id"])

    # Still exactly one user
    rows = await db.fetch(
        "SELECT * FROM social_users WHERE external_id = $1",
        "existing_user",
    )
    assert len(rows) == 1
    # Username NOT updated (process_incoming_event doesn't update existing users in Task 06)
    assert rows[0]["username"] == "old_username"


@pytest.mark.asyncio
async def test_process_event_comment_type(db) -> None:
    """Event with event_type='comment' produces source='comment'."""
    event = IncomingEvent(
        provider="sendpulse",
        platform="instagram",
        event_type="comment",
        external_user_id="commenter_1",
        external_event_id="evt_c_1",
        post_id="post_42",
        comment_id="comment_99",
        text="ОЧИЩЕНИЕ",
        occurred_at=datetime.now(UTC),
    )
    log_row = await events_repo.insert(
        provider_name="sendpulse",
        platform="instagram",
        event_type="comment",
        external_event_id="evt_c_1",
        payload={},
        signature_valid=True,
    )
    await process_incoming_event({}, event.model_dump(mode="json"), log_row["id"])

    user = await users.get_by_external("sendpulse", "instagram", "commenter_1")
    assert user is not None
    conv = await conversations.get_active(user["id"], "instagram")
    msg = await db.fetchrow(
        "SELECT * FROM messages WHERE conversation_id = $1", conv["id"],
    )
    assert msg["source"] == "comment"
    assert msg["text"] == "ОЧИЩЕНИЕ"
```

d) Создать `tests/test_heartbeat.py`:

```python
"""Tests for worker heartbeat."""
from __future__ import annotations

import pytest

from app.workers.heartbeat import (
    HEARTBEAT_KEY,
    heartbeat_age_seconds,
    heartbeat_tick,
)


@pytest.mark.asyncio
async def test_heartbeat_tick_writes_to_redis() -> None:
    from app.repos.redis_client import get_redis

    await heartbeat_tick()
    redis = await get_redis()
    value = await redis.get(HEARTBEAT_KEY)
    assert value is not None


@pytest.mark.asyncio
async def test_heartbeat_age_returns_small_value_after_tick() -> None:
    await heartbeat_tick()
    age = await heartbeat_age_seconds()
    assert age is not None
    assert age < 5.0  # should be near-zero, but allow CI slack


@pytest.mark.asyncio
async def test_heartbeat_age_returns_none_when_missing() -> None:
    from app.repos.redis_client import get_redis

    redis = await get_redis()
    await redis.delete(HEARTBEAT_KEY)
    age = await heartbeat_age_seconds()
    assert age is None
```

### 8. Обновление /ready теста

a) Обновить `tests/test_health.py` — `/ready` теперь возвращает поле `redis`:

```python
@pytest.mark.asyncio
async def test_ready_checks_postgres_and_redis(client: AsyncClient) -> None:
    response = await client.get("/ready")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["postgres"] == "up"
    assert body["redis"] == "up"
```

---

## Acceptance criteria

После выполнения всех подзадач выполнить и поставить галочки:

- [ ] Файлы созданы по структуре подзадач 1–7
- [ ] `make lint` проходит без ошибок
- [ ] `make down && make up` поднимает все 4 сервиса (api, worker, postgres, redis), все становятся healthy
- [ ] `docker compose logs worker` показывает старт arq:
  ```
  Starting worker for 1 functions: process_incoming_event
  ```
- [ ] `docker compose logs worker` показывает периодические записи `arq:queue:health` (это сам arq, не наш heartbeat)
- [ ] `redis-cli GET worker:heartbeat` (через `docker compose exec redis redis-cli`) возвращает свежую timestamp-строку (обновляется каждые 60 сек)
- [ ] `curl http://localhost:8000/ready` возвращает `{"status":"ready","postgres":"up","redis":"up"}`
- [ ] `curl -X POST http://localhost:8000/webhooks/sendpulse -H 'Content-Type: application/json' -d '{}'` возвращает `{"status":"ok"}` 200 (с FakeProvider в тестах). В production (без переопределения провайдера) пустой POST приведёт к `NotImplementedError` в SendPulseProvider.parse_webhook → exception перехватывается → события не парсятся → возвращается 200, но в `events_log` ничего не записывается. Это ожидаемо до Task 05.
- [ ] `make test` проходит, все новые тесты зелёные:
  - `test_webhook_endpoint.py` — 7 тестов
  - `test_worker_process_event.py` — 4 теста
  - `test_heartbeat.py` — 3 теста
  - `test_health.py` — обновлённый тест /ready проходит
  - Существующие тесты Tasks 01, 03, 04 продолжают работать
- [ ] Конкретный e2e-тест через FakeProvider:
  1. В тесте: создать FakeProvider, queue_event с external_event_id='manual_test'
  2. POST /webhooks/sendpulse
  3. Проверить: в events_log есть строка с этим event_id
  4. Прямой вызов process_incoming_event с этим dict
  5. Проверить: создан social_user, conversation, message
- [ ] Idempotency проверена: дважды POST с тем же external_event_id → одна запись в events_log, один пользователь, одно сообщение

---

## Do NOT

- НЕ запускать сценарии (welcome, comment-to-DM, FAQ) в воркере. Это Task 07.
- НЕ вызывать Claude API. Это Task 13.
- НЕ генерировать ответы (`OutgoingMessage`) и не отправлять через `provider.send()`. Только запись входящих в БД.
- НЕ применять safety-фильтры. Это Task 14.
- НЕ хардкодить имя провайдера в обработчике webhook. URL `/webhooks/{provider_name}` — параметр; реальный провайдер берётся через `Depends(_provider_dep)`.
- НЕ менять контракт MessagingProvider. Если воркеру нужен новый метод от провайдера — это либо метод сервиса, либо новый Task на расширение интерфейса.
- НЕ парсить JSON в webhook handler до signature check. Сначала `raw_body`, потом передаём провайдеру, провайдер сам парсит.
- НЕ возвращать 4xx/5xx из webhook endpoint. Только 200. Даже если ничего не получилось распарсить.
- НЕ добавлять лимиты на размер payload в обработчике. SendPulse шлёт payload до 1 МБ, FastAPI default это переваривает. Если нужен лимит — отдельная задача.
- НЕ добавлять зависимости вне списка из Task 01.

---

## Зависимости задачи

- Task 01 применена (есть docker-compose, FastAPI скелет, redis в стеке)
- Task 03 применена (БД, миграции, репозитории users/conversations/messages)
- Task 04 применена (MessagingProvider abstraction, FakeProvider)
- Не требует SendPulse credentials — работает на FakeProvider в тестах. В prod-режиме без credentials webhook будет принимать запросы, но `SendPulseProvider.parse_webhook` будет падать (ОК на этом этапе — Task 05 это закроет).

---

## Что после этой задачи

После применения Task 06 у нас есть полный pipeline: webhook → events_log → arq queue → worker → social_users / conversations / messages.

Дальше:

- **Task 05** — реальная реализация SendPulseProvider (требует credentials от Юли)
- **Task 07** — ScenarioEngine: после записи входящего сообщения, воркер запускает соответствующий сценарий (welcome / comment-to-DM / FAQ) и выдаёт OutgoingMessage. Здесь же — keyword matching.
- **Task 08** — Welcome scenario implementation
- **Task 09** — Comment-to-DM scenario

---

**Дата создания:** 2026-04-30
**Применять в:** `D:\Work\social_inbox` после Tasks 01, 03, 04
**Эстимейт:** 4–5 часов на Claude Code + ручная проверка
