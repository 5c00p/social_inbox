# Task 16: Monitoring

> Применить в `D:\Work\social_inbox` после Tasks 01, 03, 04, 06, 07, 08, 09, 11, 13, 14, 15. Положить файл в корень проекта, открыть в VS Code, попросить Claude Code: «Прочитай TASK_16_monitoring.md и выполни все подзадачи последовательно. Перед каждой подзадачей кратко опиши план, после — что фактически сделано».

---

## Контекст

После Task 15 у нас работающая воронка с админкой. Чего не хватает: **наблюдаемости в проде**. Если worker упадёт ночью — никто не узнает. Если Claude API начнёт фейлить — никто не отреагирует. Если Postgres переполнится — увидим только когда Юля напишет «бот сломан».

Задача — построить **trio мониторинга**:

1. **Sentry** — централизованный сбор ошибок + traces для отладки. Бесплатный план до 5k events/месяц закрывает наши потребности.
2. **Healthcheck endpoints** — `/ready` для Docker healthcheck и uptime-monitoring; `/ready/quick` для быстрых LB-проверок.
3. **Telegram alerts** — через тот же notification_bot из Task 14. Алерты только важные (worker dead, Claude down, DB outage). Дополнительно — daily digest утром.

**Принципиальное ограничение scope:** никаких Prometheus, Grafana, Datadog. Это overkill для проекта с одним VPS. Метрики смотрит Юля в админке (Task 15), ошибки идут в Sentry, критичные алерты — в Telegram.

---

## Цель

После выполнения этой задачи:

- Sentry интегрирован в `app/main.py` (FastAPI) и в worker (arq)
- `/ready` расширен: postgres + redis + worker_heartbeat
- `/ready/quick` отдельный endpoint для LB
- arq cron job каждую минуту проверяет: heartbeat, Claude failure rate, Postgres ping
- Алерты в Telegram через `notifications.notify_admin` с deduplication через Redis-флаги
- Daily digest каждое утро в 9:00 (Europe/Vilnius): лиды + handover + conversion за прошедшие сутки
- Sentry получает unhandled exceptions из api и worker; нагрузка <10% sample rate
- Тесты покрывают: alert deduplication, daily digest формирование, healthcheck endpoints
- В docker-compose добавлены healthcheck'и для api и worker

---

## Подзадачи

### 1. Зависимости

a) В `pyproject.toml` `sentry-sdk` уже есть из Task 01 (мы заложили его сразу). Проверить версию `sentry-sdk[fastapi]==2.19.2` — если нужен апгрейд, обновить.

b) Дополнительно нужен `tzdata` если deploy в slim-образе (для timezone Europe/Vilnius). Проверить и добавить если отсутствует:

```toml
# В dependencies:
"tzdata==2024.2",
```

### 2. Sentry initialization

a) Создать `app/observability/__init__.py` (пустой `__init__.py` для пакета).

b) Создать `app/observability/sentry.py`:

```python
"""Sentry initialization for FastAPI app and arq worker.

We use a single init() function called from both entry points (main.py
and arq_settings.on_startup). Sentry SDK is idempotent — calling init twice
in the same process is safe.

Config:
- DSN from settings.sentry_dsn (skip init if empty)
- Environment from settings.env (dev/prod)
- 10% trace sample rate to stay within free-tier quota
- 0% profile sample rate (profiling not needed for our scale)
- before_send filter to drop noisy errors (rate limit hits, etc.)
"""
from __future__ import annotations

from typing import Any

import sentry_sdk
from sentry_sdk.integrations.asyncio import AsyncioIntegration
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sentry_sdk.integrations.starlette import StarletteIntegration

from app.config import get_settings
from app.utils.logging import get_logger

log = get_logger(__name__)

# Errors we want to drop before sending to Sentry — they're operational, not bugs.
_NOISY_ERROR_FRAGMENTS = (
    "rate limit",
    "rate_limit_exceeded",
    "X-Internal-Token",  # 401s from probing
)


def _before_send(event: dict[str, Any], hint: dict[str, Any]) -> dict[str, Any] | None:
    """Filter out noisy errors before they consume our Sentry quota."""
    exc_info = hint.get("exc_info")
    if exc_info:
        exc_value = exc_info[1]
        text = str(exc_value).lower()
        if any(frag in text for frag in _NOISY_ERROR_FRAGMENTS):
            return None
    return event


def init_sentry(component: str) -> bool:
    """Initialize Sentry for a given component ('api' or 'worker').

    Returns True if initialized, False if skipped (no DSN or already initialized).
    """
    settings = get_settings()
    if not settings.sentry_dsn:
        log.info("sentry_skipped_no_dsn", component=component)
        return False

    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=settings.env,
        release=f"social_inbox@0.1.0",
        traces_sample_rate=0.1,
        profiles_sample_rate=0.0,
        send_default_pii=False,
        before_send=_before_send,
        integrations=[
            FastApiIntegration(transaction_style="endpoint"),
            StarletteIntegration(transaction_style="endpoint"),
            AsyncioIntegration(),
        ],
    )
    sentry_sdk.set_tag("component", component)
    log.info("sentry_initialized", component=component, env=settings.env)
    return True
```

c) Обновить `app/main.py` — вызвать `init_sentry('api')` в `lifespan` ДО других setup-шагов:

   Найти:
```python
@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    setup_logging()
    log = get_logger(__name__)
    settings = get_settings()
    log.info("startup", env=settings.env, provider=settings.messaging_provider)
```

   Заменить на:
```python
@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    setup_logging()
    init_sentry("api")
    log = get_logger(__name__)
    settings = get_settings()
    log.info("startup", env=settings.env, provider=settings.messaging_provider)
```

   Импорт: `from app.observability.sentry import init_sentry`.

d) В `app/workers/arq_settings.py` обновить `on_startup`:

   Найти:
```python
    @staticmethod
    async def on_startup(ctx: dict[str, Any]) -> None:
        # Schedule heartbeat in background loop
        import asyncio
        ctx["heartbeat_task"] = asyncio.create_task(_heartbeat_loop())
```

   Заменить на:
```python
    @staticmethod
    async def on_startup(ctx: dict[str, Any]) -> None:
        from app.observability.sentry import init_sentry
        from app.utils.logging import setup_logging

        setup_logging()
        init_sentry("worker")

        # Schedule heartbeat in background loop
        import asyncio
        ctx["heartbeat_task"] = asyncio.create_task(_heartbeat_loop())
```

### 3. Healthcheck endpoints — расширение

a) Обновить `app/api/health.py`:

```python
"""Health and readiness endpoints.

Three endpoints for different consumers:

- /health         — process liveness (always 200 if process alive)
- /ready          — full readiness check: postgres + redis + worker heartbeat
                    Used by uptime monitoring, Docker healthcheck.
- /ready/quick    — fast readiness: postgres + redis only.
                    Used by load balancers (frequent calls, lower overhead).
"""
from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, status
from fastapi.responses import JSONResponse

from app.repos.pool import ping as pg_ping
from app.repos.redis_client import ping as redis_ping
from app.workers.heartbeat import HEARTBEAT_TTL_SECONDS, heartbeat_age_seconds

router = APIRouter(tags=["health"])

# Worker is considered unhealthy if heartbeat is older than this.
WORKER_STALE_THRESHOLD_SECONDS = 180  # 3 minutes


@router.get("/health")
async def health() -> dict[str, str]:
    """Liveness probe — returns ok if process is up."""
    return {"status": "ok"}


@router.get("/ready/quick")
async def ready_quick() -> JSONResponse:
    """Quick readiness — postgres + redis only.

    Use for high-frequency LB checks (every few seconds).
    """
    pg_ok = await pg_ping()
    redis_ok = await redis_ping()
    body = {
        "status": "ready" if (pg_ok and redis_ok) else "not_ready",
        "postgres": "up" if pg_ok else "down",
        "redis": "up" if redis_ok else "down",
    }
    code = status.HTTP_200_OK if (pg_ok and redis_ok) else status.HTTP_503_SERVICE_UNAVAILABLE
    return JSONResponse(status_code=code, content=body)


@router.get("/ready")
async def ready() -> JSONResponse:
    """Full readiness — postgres + redis + worker heartbeat.

    Use for monitoring / Docker healthcheck (low frequency, every 30s+).
    Returns 503 if any component is down.
    """
    pg_ok = await pg_ping()
    redis_ok = await redis_ping()

    worker_age = await heartbeat_age_seconds()
    worker_ok = worker_age is not None and worker_age < WORKER_STALE_THRESHOLD_SECONDS

    body = {
        "status": "ready" if (pg_ok and redis_ok and worker_ok) else "not_ready",
        "postgres": "up" if pg_ok else "down",
        "redis": "up" if redis_ok else "down",
        "worker": {
            "status": "up" if worker_ok else "down",
            "heartbeat_age_seconds": worker_age,
        },
        "checked_at": datetime.now(UTC).isoformat(),
    }
    all_ok = pg_ok and redis_ok and worker_ok
    code = status.HTTP_200_OK if all_ok else status.HTTP_503_SERVICE_UNAVAILABLE
    return JSONResponse(status_code=code, content=body)
```

### 4. Alert deduplication

a) Создать `app/observability/alerts.py`:

```python
"""Operational alerts to admin via Telegram, with deduplication.

Why dedup: if worker is dead for an hour, we don't want 60 alerts.
Pattern: each alert type has a TTL window. Within the window, repeated
fires are suppressed. After TTL expires, alert fires again (so admin
knows the issue is ongoing).

Alert types and their TTLs:
- worker_dead              30 min  — heartbeat stale
- claude_failures           15 min  — Claude API errors above threshold
- postgres_down              5 min  — DB unreachable
- webhook_parse_failures    30 min  — too many parse errors from provider
"""
from __future__ import annotations

from typing import Literal

from app.repos.redis_client import get_redis
from app.services import notifications
from app.utils.logging import get_logger

log = get_logger(__name__)

AlertType = Literal[
    "worker_dead",
    "claude_failures",
    "postgres_down",
    "webhook_parse_failures",
]

DEDUP_TTL_BY_TYPE: dict[AlertType, int] = {
    "worker_dead": 30 * 60,
    "claude_failures": 15 * 60,
    "postgres_down": 5 * 60,
    "webhook_parse_failures": 30 * 60,
}


def _dedup_key(alert_type: AlertType) -> str:
    return f"alert:dedup:{alert_type}"


async def fire_alert(
    alert_type: AlertType,
    message: str,
) -> bool:
    """Send an alert to admin if not already fired within the dedup window.

    Returns True if alert was sent, False if suppressed by dedup.
    """
    redis = await get_redis()
    key = _dedup_key(alert_type)
    ttl = DEDUP_TTL_BY_TYPE[alert_type]

    # SET NX with EX: only set if not exists, with expiry
    was_set = await redis.set(key, "1", nx=True, ex=ttl)
    if not was_set:
        log.debug("alert_suppressed_dedup", type=alert_type)
        return False

    full_message = f"🚨 *{alert_type}*\n\n{message}"
    await notifications.notify_admin(full_message)
    log.warning("alert_fired", type=alert_type, message=message[:200])
    return True


async def reset_dedup(alert_type: AlertType) -> None:
    """Manually reset a dedup window. Useful when issue is acknowledged
    so the admin can be re-notified if it recurs immediately.
    """
    redis = await get_redis()
    await redis.delete(_dedup_key(alert_type))
```

### 5. Claude failure tracker

a) Создать `app/observability/claude_health.py`:

```python
"""Track Claude API failure rate via Redis sliding window.

Why: Claude API can have hiccups (rate limits, regional outages). We want
to know when failures spike, not on first error. Sliding window approach:
- Increment failure counter on each error
- Increment success counter on each successful call
- After N attempts, check ratio. If failure_rate > threshold, alert.

Window: last 5 minutes. Sliding via Redis EXPIRE on counter keys.

Thresholds:
- min_attempts = 5  — don't alert on a single failure (could be transient)
- failure_rate_threshold = 0.5 — alert if 50%+ of recent calls fail
"""
from __future__ import annotations

from app.observability.alerts import fire_alert
from app.repos.redis_client import get_redis
from app.utils.logging import get_logger

log = get_logger(__name__)

WINDOW_SECONDS = 5 * 60
SUCCESS_KEY = "claude:health:success"
FAILURE_KEY = "claude:health:failure"

MIN_ATTEMPTS = 5
FAILURE_RATE_THRESHOLD = 0.5


async def record_success() -> None:
    redis = await get_redis()
    pipe = redis.pipeline()
    pipe.incr(SUCCESS_KEY)
    pipe.expire(SUCCESS_KEY, WINDOW_SECONDS)
    await pipe.execute()


async def record_failure() -> None:
    redis = await get_redis()
    pipe = redis.pipeline()
    pipe.incr(FAILURE_KEY)
    pipe.expire(FAILURE_KEY, WINDOW_SECONDS)
    await pipe.execute()
    await _check_threshold()


async def _check_threshold() -> None:
    redis = await get_redis()
    successes = int(await redis.get(SUCCESS_KEY) or 0)
    failures = int(await redis.get(FAILURE_KEY) or 0)
    total = successes + failures

    if total < MIN_ATTEMPTS:
        return

    failure_rate = failures / total
    if failure_rate >= FAILURE_RATE_THRESHOLD:
        await fire_alert(
            "claude_failures",
            f"Claude API failure rate: {failure_rate:.0%} "
            f"({failures}/{total} calls in last {WINDOW_SECONDS // 60} min). "
            f"Возможен сбой API или превышение rate-limit Anthropic.",
        )
```

b) Подключить tracker в `app/services/claude_responder.py`. Найти try/except вокруг `client.messages.create`:

   Заменить:
```python
    try:
        response = await client.messages.create(...)
    except APIStatusError as exc:
        log.warning(...)
        return None
    except APIError as exc:
        log.warning(...)
        return None
    except Exception as exc:
        log.exception(...)
        return None
```

   На:
```python
    from app.observability import claude_health

    try:
        response = await client.messages.create(
            model=chosen_model,
            max_tokens=max_tokens,
            system=system_prompt,
            tools=TOOLS,
            messages=api_messages,
        )
    except APIStatusError as exc:
        log.warning(
            "claude_api_status_error",
            user_id=user_id,
            status=exc.status_code,
            message=str(exc)[:200],
        )
        await claude_health.record_failure()
        return None
    except APIError as exc:
        log.warning("claude_api_error", user_id=user_id, error=str(exc)[:200])
        await claude_health.record_failure()
        return None
    except Exception as exc:
        log.exception("claude_unexpected_error", user_id=user_id, error=str(exc))
        await claude_health.record_failure()
        return None

    await claude_health.record_success()
```

   Импорт `claude_health` поднять наверх файла.

### 6. Watchdog cron job

a) Обновить `app/workers/arq_settings.py` — добавить cron-job для watchdog. Финальный вид WorkerSettings:

```python
"""arq worker configuration.

Run worker:
    arq app.workers.arq_settings.WorkerSettings
"""
from __future__ import annotations

from typing import Any

from arq import cron
from arq.connections import RedisSettings

from app.config import get_settings
from app.workers.heartbeat import heartbeat_tick
from app.workers.tasks_messages import process_incoming_event
from app.workers.tasks_watchdog import (
    daily_digest,
    watchdog_check,
)


def _redis_settings() -> RedisSettings:
    settings = get_settings()
    return RedisSettings.from_dsn(settings.redis_url)


class WorkerSettings:
    """Settings consumed by `arq` CLI runner."""

    redis_settings = _redis_settings()

    functions: list[Any] = [process_incoming_event, watchdog_check, daily_digest]

    cron_jobs = [
        # Watchdog: every minute
        cron(watchdog_check, minute=set(range(60)), run_at_startup=False),
        # Daily digest: 09:00 Europe/Vilnius (UTC+2 winter, UTC+3 summer).
        # We schedule by UTC; pick 07:00 UTC ≈ 09:00–10:00 local.
        cron(daily_digest, hour={7}, minute={0}, run_at_startup=False),
    ]

    max_jobs = 10
    job_timeout = 60
    keep_result = 60
    max_tries = 3
    log_results = True

    @staticmethod
    async def on_startup(ctx: dict[str, Any]) -> None:
        from app.observability.sentry import init_sentry
        from app.utils.logging import setup_logging

        setup_logging()
        init_sentry("worker")

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
            pass
        await asyncio.sleep(60)
```

### 7. Watchdog tasks

a) Создать `app/workers/tasks_watchdog.py`:

```python
"""Watchdog and reporting tasks running on cron schedule.

watchdog_check (every minute):
- Verify postgres ping
- Verify worker heartbeat is fresh
  (this runs in worker itself, so heartbeat being stale here is unusual —
   it would mean the heartbeat loop crashed)
- Trigger alerts via app.observability.alerts (with dedup)

daily_digest (09:00 local):
- Aggregate yesterday's metrics
- Send Telegram message to admin
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from app.observability.alerts import fire_alert
from app.repos.pool import get_pool, ping as pg_ping
from app.services import notifications
from app.utils.logging import get_logger
from app.workers.heartbeat import heartbeat_age_seconds

log = get_logger(__name__)

WORKER_STALE_THRESHOLD_SECONDS = 180


async def watchdog_check(ctx: dict[str, Any]) -> None:
    """Run health checks and fire alerts on issues."""
    log.debug("watchdog_tick_start")

    # 1. Postgres
    pg_ok = await pg_ping()
    if not pg_ok:
        await fire_alert(
            "postgres_down",
            "Postgres unreachable from worker. "
            "Проверь VPS, контейнер postgres, диск.",
        )

    # 2. Worker heartbeat (sanity check — should always be fresh since we're worker ourselves)
    age = await heartbeat_age_seconds()
    if age is None or age >= WORKER_STALE_THRESHOLD_SECONDS:
        await fire_alert(
            "worker_dead",
            f"Heartbeat is stale: age={age}s "
            f"(threshold {WORKER_STALE_THRESHOLD_SECONDS}s). "
            f"Heartbeat loop may have crashed.",
        )

    log.debug("watchdog_tick_done", postgres_ok=pg_ok, heartbeat_age=age)


async def daily_digest(ctx: dict[str, Any]) -> None:
    """Send daily summary to admin: yesterday's metrics."""
    log.info("daily_digest_running")

    pool = await get_pool()
    yesterday_start_utc = (datetime.now(UTC) - timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0,
    )
    yesterday_end_utc = yesterday_start_utc + timedelta(days=1)

    new_leads = await pool.fetchval(
        """
        SELECT COUNT(*)::int FROM social_users
        WHERE first_seen_at >= $1 AND first_seen_at < $2
          AND deleted_at IS NULL
        """,
        yesterday_start_utc, yesterday_end_utc,
    )

    handovers = await pool.fetchval(
        """
        SELECT COUNT(*)::int FROM conversations
        WHERE status IN ('handover_pending', 'handover_done')
          AND last_message_at >= $1 AND last_message_at < $2
        """,
        yesterday_start_utc, yesterday_end_utc,
    )

    tg_handovers = await pool.fetchval(
        """
        SELECT COUNT(*)::int FROM social_users
        WHERE tg_handover_at >= $1 AND tg_handover_at < $2
        """,
        yesterday_start_utc, yesterday_end_utc,
    )

    claude_calls = await pool.fetchval(
        """
        SELECT COUNT(*)::int FROM messages
        WHERE created_at >= $1 AND created_at < $2
          AND claude_used = TRUE
        """,
        yesterday_start_utc, yesterday_end_utc,
    )

    claude_blocked = await pool.fetchval(
        """
        SELECT COUNT(*)::int FROM messages
        WHERE created_at >= $1 AND created_at < $2
          AND safety_blocked = TRUE
        """,
        yesterday_start_utc, yesterday_end_utc,
    )

    date_label = yesterday_start_utc.strftime("%Y-%m-%d")
    text = (
        f"📊 *Сводка за {date_label}*\n\n"
        f"🆕 Новых лидов: *{new_leads}*\n"
        f"📲 Дошли до Telegram: *{tg_handovers}*\n"
        f"👤 Эскалации: *{handovers}*\n"
        f"🤖 Ответов через Claude: *{claude_calls}*\n"
        f"🛑 Заблокировано safety: *{claude_blocked}*\n\n"
        f"_Подробнее — в админке._"
    )
    await notifications.notify_admin(text)
    log.info(
        "daily_digest_sent",
        new_leads=new_leads,
        handovers=handovers,
        tg_handovers=tg_handovers,
        claude_calls=claude_calls,
    )
```

### 8. Webhook parse failure tracking

a) В `app/api/webhooks.py` найти блок:

```python
    try:
        events = await provider.parse_webhook(raw_body, headers)
    except Exception as exc:
        log.exception("webhook_parse_failed", provider=provider_name, error=str(exc))
        events = []
```

   Заменить на:

```python
    try:
        events = await provider.parse_webhook(raw_body, headers)
    except Exception as exc:
        log.exception("webhook_parse_failed", provider=provider_name, error=str(exc))
        await _record_parse_failure()
        events = []
```

b) Добавить функцию учёта парс-ошибок в тот же файл (внизу):

```python
async def _record_parse_failure() -> None:
    """Increment parse failure counter; alert if threshold breached."""
    from app.observability.alerts import fire_alert
    from app.repos.redis_client import get_redis

    redis = await get_redis()
    key = "webhook:parse_failures"
    count = await redis.incr(key)
    if count == 1:
        await redis.expire(key, 3600)  # 1-hour window

    if count >= 10:
        await fire_alert(
            "webhook_parse_failures",
            f"Получено {count} ошибок парсинга webhook за последний час. "
            f"Возможны проблемы у провайдера или изменения в их API.",
        )
```

### 9. Docker compose healthchecks

a) В `docker-compose.yml` добавить healthcheck для `api` и `worker`:

   Для api:
```yaml
  api:
    # ...existing config...
    healthcheck:
      test: ["CMD", "python", "-c",
             "import urllib.request,sys; r=urllib.request.urlopen('http://localhost:8000/ready/quick',timeout=3); sys.exit(0 if r.status==200 else 1)"]
      interval: 30s
      timeout: 5s
      retries: 3
      start_period: 30s
```

   Для worker (нет HTTP endpoint, проверяем heartbeat через Redis):
```yaml
  worker:
    # ...existing config...
    healthcheck:
      test: ["CMD", "python", "-c",
             "import asyncio,sys; from app.workers.heartbeat import heartbeat_age_seconds; age=asyncio.run(heartbeat_age_seconds()); sys.exit(0 if (age is not None and age<300) else 1)"]
      interval: 60s
      timeout: 10s
      retries: 3
      start_period: 90s
```

   Не делаем healthcheck для admin — Streamlit имеет свой `/_stcore/health` (используется в Task 15 acceptance).

### 10. Конфигурация

a) В `app/config.py` уже есть `sentry_dsn`. Убедиться что есть `notification_bot_token` и `notification_admin_chat_id` (из Task 14).

b) В `.env.example` добавить пояснительные комментарии к этим полям:

```bash
# --- Monitoring ---
# Sentry: https://sentry.io/ — register, create project "social_inbox", get DSN
# Free tier (5k events/month) is enough for this scale
SENTRY_DSN=

# --- Notification bot (admin alerts) ---
# Same Telegram bot used by Task 14 (handover) + Task 16 (alerts/digest)
NOTIFICATION_BOT_TOKEN=
NOTIFICATION_ADMIN_CHAT_ID=
```

### 11. Тесты

a) Создать `tests/test_alerts.py`:

```python
"""Tests for alert deduplication."""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.observability import alerts
from app.repos.redis_client import get_redis
from app.services import notifications


@pytest.fixture(autouse=True)
async def _clear_dedup_keys() -> None:
    redis = await get_redis()
    keys = await redis.keys("alert:dedup:*")
    if keys:
        await redis.delete(*keys)
    yield
    keys = await redis.keys("alert:dedup:*")
    if keys:
        await redis.delete(*keys)


@pytest.mark.asyncio
async def test_first_alert_fires(monkeypatch: pytest.MonkeyPatch) -> None:
    notify_mock = AsyncMock(return_value=True)
    monkeypatch.setattr(notifications, "notify_admin", notify_mock)

    fired = await alerts.fire_alert("worker_dead", "test message")
    assert fired is True
    notify_mock.assert_called_once()


@pytest.mark.asyncio
async def test_duplicate_alert_suppressed(monkeypatch: pytest.MonkeyPatch) -> None:
    notify_mock = AsyncMock(return_value=True)
    monkeypatch.setattr(notifications, "notify_admin", notify_mock)

    await alerts.fire_alert("worker_dead", "first")
    fired_again = await alerts.fire_alert("worker_dead", "second within window")

    assert fired_again is False
    notify_mock.assert_called_once()


@pytest.mark.asyncio
async def test_different_alert_types_independent(monkeypatch: pytest.MonkeyPatch) -> None:
    notify_mock = AsyncMock(return_value=True)
    monkeypatch.setattr(notifications, "notify_admin", notify_mock)

    await alerts.fire_alert("worker_dead", "x")
    await alerts.fire_alert("postgres_down", "y")

    assert notify_mock.call_count == 2


@pytest.mark.asyncio
async def test_reset_dedup_allows_refire(monkeypatch: pytest.MonkeyPatch) -> None:
    notify_mock = AsyncMock(return_value=True)
    monkeypatch.setattr(notifications, "notify_admin", notify_mock)

    await alerts.fire_alert("worker_dead", "first")
    await alerts.reset_dedup("worker_dead")
    fired = await alerts.fire_alert("worker_dead", "after reset")

    assert fired is True
    assert notify_mock.call_count == 2
```

b) Создать `tests/test_claude_health.py`:

```python
"""Tests for Claude failure rate tracking."""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.observability import alerts, claude_health
from app.repos.redis_client import get_redis
from app.services import notifications


@pytest.fixture(autouse=True)
async def _clear_health_keys() -> None:
    redis = await get_redis()
    keys = await redis.keys("claude:health:*")
    keys.extend(await redis.keys("alert:dedup:*"))
    if keys:
        await redis.delete(*keys)
    yield
    keys = await redis.keys("claude:health:*")
    keys.extend(await redis.keys("alert:dedup:*"))
    if keys:
        await redis.delete(*keys)


@pytest.mark.asyncio
async def test_no_alert_below_min_attempts(monkeypatch: pytest.MonkeyPatch) -> None:
    notify_mock = AsyncMock(return_value=True)
    monkeypatch.setattr(notifications, "notify_admin", notify_mock)

    # 4 failures — below MIN_ATTEMPTS (5)
    for _ in range(4):
        await claude_health.record_failure()

    notify_mock.assert_not_called()


@pytest.mark.asyncio
async def test_alert_fires_above_threshold(monkeypatch: pytest.MonkeyPatch) -> None:
    notify_mock = AsyncMock(return_value=True)
    monkeypatch.setattr(notifications, "notify_admin", notify_mock)

    # 5 failures, 0 successes → 100% failure rate
    for _ in range(5):
        await claude_health.record_failure()

    notify_mock.assert_called_once()
    call_text = notify_mock.call_args.args[0]
    assert "claude_failures" in call_text


@pytest.mark.asyncio
async def test_no_alert_when_mostly_successes(monkeypatch: pytest.MonkeyPatch) -> None:
    notify_mock = AsyncMock(return_value=True)
    monkeypatch.setattr(notifications, "notify_admin", notify_mock)

    # 9 successes, 1 failure = 10% rate, below threshold
    for _ in range(9):
        await claude_health.record_success()
    await claude_health.record_failure()

    notify_mock.assert_not_called()
```

c) Создать `tests/test_health_endpoints.py`:

```python
"""Tests for /health, /ready, /ready/quick endpoints."""
from __future__ import annotations

import pytest
from httpx import AsyncClient

from app.repos.redis_client import get_redis
from app.workers.heartbeat import HEARTBEAT_KEY, heartbeat_tick


@pytest.mark.asyncio
async def test_health_always_ok(client: AsyncClient) -> None:
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_ready_quick_with_pg_redis_up(client: AsyncClient) -> None:
    response = await client.get("/ready/quick")
    assert response.status_code == 200
    body = response.json()
    assert body["postgres"] == "up"
    assert body["redis"] == "up"


@pytest.mark.asyncio
async def test_ready_full_requires_worker_heartbeat(client: AsyncClient) -> None:
    redis = await get_redis()
    await redis.delete(HEARTBEAT_KEY)

    # No heartbeat → /ready returns 503
    r1 = await client.get("/ready")
    assert r1.status_code == 503
    body1 = r1.json()
    assert body1["worker"]["status"] == "down"

    # After heartbeat tick → 200
    await heartbeat_tick()
    r2 = await client.get("/ready")
    assert r2.status_code == 200
    body2 = r2.json()
    assert body2["worker"]["status"] == "up"
    assert body2["status"] == "ready"
```

d) Создать `tests/test_daily_digest.py`:

```python
"""Tests for daily digest task."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from app.repos import users
from app.services import notifications
from app.workers.tasks_watchdog import daily_digest


@pytest.mark.asyncio
async def test_daily_digest_sends_admin_message(
    db, monkeypatch: pytest.MonkeyPatch,
) -> None:
    notify_mock = AsyncMock(return_value=True)
    monkeypatch.setattr(notifications, "notify_admin", notify_mock)

    # Seed a user "yesterday"
    yesterday = datetime.now(UTC) - timedelta(days=1, hours=2)
    user = await users.create(
        provider_name="sendpulse", platform="instagram",
        external_id="digest_user_1",
    )
    # Backdate first_seen_at
    await db.execute(
        "UPDATE social_users SET first_seen_at = $2 WHERE id = $1",
        user["id"], yesterday,
    )

    await daily_digest({})

    notify_mock.assert_called_once()
    text = notify_mock.call_args.args[0]
    assert "Сводка" in text
    assert "Новых лидов" in text


@pytest.mark.asyncio
async def test_daily_digest_zero_when_no_data(
    db, monkeypatch: pytest.MonkeyPatch,
) -> None:
    notify_mock = AsyncMock(return_value=True)
    monkeypatch.setattr(notifications, "notify_admin", notify_mock)

    await daily_digest({})

    notify_mock.assert_called_once()
    text = notify_mock.call_args.args[0]
    # Even with no data, message structure is correct
    assert "Сводка" in text
    assert "*0*" in text  # zeros for fields
```

e) Создать `tests/test_watchdog.py`:

```python
"""Tests for watchdog_check task."""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.observability import alerts
from app.repos.redis_client import get_redis
from app.services import notifications
from app.workers import tasks_watchdog
from app.workers.heartbeat import HEARTBEAT_KEY, heartbeat_tick


@pytest.fixture(autouse=True)
async def _clear_dedup() -> None:
    redis = await get_redis()
    keys = await redis.keys("alert:dedup:*")
    if keys:
        await redis.delete(*keys)
    yield


@pytest.mark.asyncio
async def test_watchdog_alerts_on_stale_heartbeat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    notify_mock = AsyncMock(return_value=True)
    monkeypatch.setattr(notifications, "notify_admin", notify_mock)

    redis = await get_redis()
    await redis.delete(HEARTBEAT_KEY)

    await tasks_watchdog.watchdog_check({})

    # worker_dead alert fired
    assert any(
        "worker_dead" in str(call.args[0])
        for call in notify_mock.call_args_list
    )


@pytest.mark.asyncio
async def test_watchdog_silent_when_healthy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    notify_mock = AsyncMock(return_value=True)
    monkeypatch.setattr(notifications, "notify_admin", notify_mock)

    await heartbeat_tick()  # fresh heartbeat

    await tasks_watchdog.watchdog_check({})

    notify_mock.assert_not_called()
```

---

## Acceptance criteria

- [ ] Файлы созданы по структуре подзадач 1–10
- [ ] `make lint` проходит
- [ ] `make test` проходит, все новые тесты зелёные:
  - `test_alerts.py` — 4 теста
  - `test_claude_health.py` — 3 теста
  - `test_health_endpoints.py` — 3 теста
  - `test_daily_digest.py` — 2 теста
  - `test_watchdog.py` — 2 теста
  - Все существующие тесты Tasks 01, 03, 04, 06, 07, 08, 09, 11, 13, 14, 15 продолжают работать
- [ ] Без `SENTRY_DSN` приложение стартует, в логах `sentry_skipped_no_dsn`
- [ ] С `SENTRY_DSN` (тестовый ключ) старт API: в логах `sentry_initialized component=api`
- [ ] `curl http://localhost:8000/ready/quick` возвращает 200 с полями postgres/redis
- [ ] `curl http://localhost:8000/ready` возвращает 200 если worker тикает heartbeat; 503 если heartbeat пропал больше 3 минут (можно симулировать остановкой worker контейнера)
- [ ] `docker compose ps` показывает api и worker как `healthy` (после start_period)
- [ ] При ручной остановке worker'а (`docker compose stop worker`) через 3-5 минут в Telegram приходит alert «worker_dead»
- [ ] При повторных ошибках (worker dead 30+ минут) НЕ приходит спам — дедуп работает
- [ ] Daily digest: можно вручную вызвать через `docker compose exec api python -c "import asyncio; from app.workers.tasks_watchdog import daily_digest; asyncio.run(daily_digest({}))"` — Юля получает сводку за вчера

---

## Do NOT

- НЕ ставить `traces_sample_rate=1.0` в production. 10% — компромисс между детализацией и стоимостью Sentry.
- НЕ слать алерт на КАЖДУЮ ошибку Claude. Используем sliding window threshold — иначе Sentry/Telegram превратятся в шум.
- НЕ хранить Sentry events с PII. `send_default_pii=False` принципиально — иначе утечёт user_id и тексты сообщений (в т.ч. медицинских) в Sentry, что нарушает privacy.
- НЕ использовать `before_send` для подавления реальных багов. Только operational noise (rate limit hits, 401 от probing).
- НЕ запускать daily_digest чаще раза в сутки (через дублирование cron). Юля не должна получать одинаковые сводки несколько раз.
- НЕ объединять notification_bot и bot_purify. Технический бот для алертов — отдельный (создан в Task 14).
- НЕ использовать `pytz` — устарел. Stdlib `zoneinfo` (Python 3.12) вполне справляется. Хотя в этой задаче time зон через cron-job в UTC — самый простой подход.
- НЕ добавлять Prometheus/Grafana. Метрики — в админке (Task 15), ошибки — в Sentry, алерты — в Telegram. Этого достаточно.
- НЕ ставить watchdog interval ниже минуты. Это и есть минимум, ниже — нагрузка не оправдана.
- НЕ забыть `start_period: 30s` (api) и `90s` (worker) в healthcheck — иначе Docker пометит как unhealthy до того, как сервис запустится.

---

## Зависимости задачи

- Tasks 01, 03, 04, 06, 07, 08, 09, 11, 13, 14, 15 применены
- В `.env`:
  - `SENTRY_DSN` (опционально для dev, обязательно для prod)
  - `NOTIFICATION_BOT_TOKEN` + `NOTIFICATION_ADMIN_CHAT_ID` (из Task 14, переиспользуем)
- Юля или Виктор зарегистрировал проект на Sentry.io (бесплатно, 5 минут)

---

## Что после этой задачи

После применения у нас полная наблюдаемость:

```
✅ Sentry собирает unhandled exceptions из api и worker
✅ /ready endpoint показывает состояние postgres + redis + worker
✅ /ready/quick для load balancer'а
✅ Telegram alerts при: worker dead, postgres down, claude failures, webhook parse errors
✅ Alert dedup — один алерт на инцидент, не флуд
✅ Daily digest утром — Юля видит вчерашние метрики без открытия админки
✅ Docker healthcheck-и для api и worker
```

Дальше:

- **Task 05** — SendPulseProvider (если ещё не сделана)
- **Task 17** — Production deployment (VPS, Traefik с HTTPS, перенос в прод)
- **Task 18** — Smoke tests + go-live checklist

После Tasks 05 + 17 — проект **развёрнут**. После Task 18 — **формально запущен**.

---

**Дата создания:** 2026-05-08
**Применять в:** `D:\Work\social_inbox` после Tasks 01, 03, 04, 06, 07, 08, 09, 11, 13, 14, 15
**Эстимейт:** 4–5 часов на Claude Code + ручная проверка
