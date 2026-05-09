# Task 11: /api/lead/{short_id} endpoint

> Применить в `D:\Work\social_inbox` после Tasks 01, 03, 04, 06, 07, 08, 09. Положить файл в корень проекта, открыть в VS Code, попросить Claude Code: «Прочитай TASK_11_lead_endpoint.md и выполни все подзадачи последовательно. Перед каждой подзадачей кратко опиши план, после — что фактически сделано».

---

## Контекст

Это критическая задача для замыкания интеграции с bot_purify.

После Tasks 08 и 09 у нас работают два сценария — welcome и comment-to-DM — оба отправляют пользователю DM со ссылкой `https://t.me/yuliya_purify_bot?start=ig_<short_id>_<scenario_slug>`. Пользователь нажимает → попадает в bot_purify.

Что делает bot_purify (после применения `TASK_social_inbox_integration.md`):
- Парсит payload `ig_<short_id>_<scenario_slug>`
- Вызывает `GET /api/lead/{short_id}` к нашему сервису
- Получает контекст лида (имя, scenario, история сообщений)
- Показывает персонализированное приветствие → запускает квиз

**Этого endpoint'а у нас пока нет.** Это последнее звено цепочки.

После Task 11 у нас работает **полный e2e через реальные системы** (если Tasks 05 и SendPulse уже подключены — впрочем, тесты Task 11 идут через FakeProvider и настоящего bot_purify не требуют).

Дополнительный endpoint в этой задаче — `POST /api/lead/{short_id}/handover` — bot_purify дёргает его при `/start`-обработке, чтобы зафиксировать факт перехода и связать TG-юзера с social-юзером. Это даёт метрику conversion: «сколько лидов реально дошло из IG до Telegram».

---

## Цель

После выполнения этой задачи:

- Существует `GET /api/lead/{short_id}` — отдаёт контекст лида
- Существует `POST /api/lead/{short_id}/handover` — фиксирует переход в Telegram
- Аутентификация через `X-Internal-Token` (constant-time compare)
- Pydantic-модели ответа покрывают все поля из контракта в CLAUDE.md § 9.2
- `scenario_slug` восстанавливается из последнего outgoing message → `scenarios.metadata.tg_scenario_slug`
- Recent messages: 10 последних, в chronological order
- Rate limiting: 60 RPM на токен
- Логирование запросов без утечки PII в логи
- Тесты покрывают: успешный fetch, 401 без токена, 404 на несуществующий short_id, handover с обновлением tg_user_id, rate limit
- E2E тест через FakeProvider: webhook → DM → deep-link → /api/lead → корректный ответ

---

## Подзадачи

### 1. Pydantic-модели ответа

a) Создать `app/models/lead.py`:

```python
"""Pydantic models for /api/lead/{short_id} endpoint.

These models are the **public contract** with bot_purify.
Any change requires coordinated update in bot_purify/services/social_inbox.py.

Versioning policy: this is a v1 contract. Breaking changes require new path
(/api/v2/lead/...) and parallel deployment with deprecation period.
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import Direction, Platform


class LeadUserInfo(BaseModel):
    """Subset of social_users data exposed to bot_purify."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    platform: Platform
    username: str | None = None
    full_name: str | None = None
    first_seen_at: datetime


class LeadMessage(BaseModel):
    """A single message in lead's conversation history.

    Only direction + text + timestamp — no internal metadata
    (claude_tokens, raw_payload, scenario_id, etc.) to keep contract minimal.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    direction: Direction
    text: str | None = None
    created_at: datetime


class LeadResponse(BaseModel):
    """Response body for GET /api/lead/{short_id}.

    Contract documented in CLAUDE.md § 9.2.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    user: LeadUserInfo
    scenario: str = Field(
        description=(
            "scenario_slug from the deep-link that brought the user "
            "(e.g. 'purify', 'oils', 'faq'). Falls back to 'unknown' "
            "if no outgoing message with a slug exists yet."
        )
    )
    recent_messages: list[LeadMessage]


class HandoverRequest(BaseModel):
    """Body for POST /api/lead/{short_id}/handover."""

    model_config = ConfigDict(extra="forbid")

    tg_user_id: int = Field(gt=0, description="Telegram user ID that landed in bot_purify")


class HandoverResponse(BaseModel):
    """Response for POST /api/lead/{short_id}/handover."""

    model_config = ConfigDict(extra="forbid")

    status: str
    tg_user_id: int
    handed_over_at: datetime
```

### 2. Расширение репозитория users

a) Добавить в `app/repos/users.py` функцию для получения последних outgoing-сообщений с метаданными scenarios (понадобится для извлечения slug):

```python
async def get_last_outgoing_with_scenario(user_id: int) -> asyncpg.Record | None:
    """Return the most recent OUT message together with its scenario metadata.

    Used by /api/lead/{short_id} to determine which scenario_slug brought
    the user to Telegram (the deep-link in this message contains it).
    """
    pool = await get_pool()
    return await pool.fetchrow(
        """
        SELECT m.id AS message_id,
               m.created_at,
               s.metadata AS scenario_metadata,
               s.name AS scenario_name
        FROM messages m
        JOIN conversations c ON c.id = m.conversation_id
        LEFT JOIN scenarios s ON s.id = m.scenario_id
        WHERE c.user_id = $1
          AND m.direction = 'out'
        ORDER BY m.created_at DESC
        LIMIT 1
        """,
        user_id,
    )
```

### 3. Расширение репозитория messages

a) Добавить в `app/repos/messages.py` функцию для получения сообщений лида в chronological order:

```python
async def get_recent_for_user(
    user_id: int,
    limit: int = 10,
) -> list[asyncpg.Record]:
    """Return last N messages across ALL conversations of the user, oldest first.

    Note: in our model a user has one conversation per platform.
    For lead endpoint, we want the entire visible history regardless of platform —
    bot_purify uses this for context, not for filtering.
    """
    pool = await get_pool()
    rows = await pool.fetch(
        """
        SELECT m.direction, m.text, m.created_at
        FROM messages m
        JOIN conversations c ON c.id = m.conversation_id
        WHERE c.user_id = $1
        ORDER BY m.created_at DESC
        LIMIT $2
        """,
        user_id, limit,
    )
    return list(reversed(rows))  # caller wants oldest first
```

### 4. Auth helper

a) Создать `app/api/auth.py`:

```python
"""Authentication helpers for internal API endpoints.

X-Internal-Token is a shared secret between social_inbox and bot_purify.
Uses constant-time comparison to prevent timing-based extraction.
"""
from __future__ import annotations

import secrets

from fastapi import Header, HTTPException, status

from app.config import get_settings


def verify_internal_token(
    x_internal_token: str | None = Header(default=None, alias="X-Internal-Token"),
) -> None:
    """FastAPI dependency: validate X-Internal-Token header.

    Returns nothing on success; raises 401 on failure.
    """
    settings = get_settings()
    expected = settings.internal_api_token

    if not x_internal_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing X-Internal-Token header",
        )

    if not secrets.compare_digest(x_internal_token, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid X-Internal-Token",
        )
```

### 5. Rate limiting helper для API

a) Расширить `app/services/rate_limiter.py` — добавить функцию для лимита API-токена:

```python
# Add to app/services/rate_limiter.py:

API_REQUESTS_PER_MINUTE = 60
API_WINDOW_SECONDS = 60


async def can_call_internal_api(token_fingerprint: str) -> bool:
    """Returns True if internal API caller is within rate limit.

    Token is hashed before use as Redis key (don't put secrets in keys).
    """
    key = f"rl:api:{token_fingerprint}"
    return await check_and_increment(
        key, API_REQUESTS_PER_MINUTE, API_WINDOW_SECONDS,
    )
```

b) Хеш токена для Redis-ключа реализовать в `app/api/auth.py` (т.к. endpoint видит токен в заголовке):

   Добавить в `app/api/auth.py`:

```python
import hashlib


def fingerprint_token(token: str) -> str:
    """Stable short fingerprint of a token, safe to use as Redis key.

    SHA-256 hex truncated to 16 chars. NOT for security — only for grouping
    requests by caller in rate-limit accounting.
    """
    return hashlib.sha256(token.encode()).hexdigest()[:16]
```

### 6. Lead endpoint

a) Создать `app/api/lead.py`:

```python
"""Lead context endpoint — entry point for bot_purify.

Endpoints:
- GET  /api/lead/{short_id}            — fetch lead context
- POST /api/lead/{short_id}/handover   — record successful Telegram handover

Auth: X-Internal-Token (shared secret with bot_purify).
Rate limit: 60 RPM per token fingerprint.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, status

from app.api.auth import fingerprint_token, verify_internal_token
from app.models.lead import (
    HandoverRequest,
    HandoverResponse,
    LeadMessage,
    LeadResponse,
    LeadUserInfo,
)
from app.repos import messages as messages_repo, users as users_repo
from app.services import lead_tracker
from app.services.rate_limiter import can_call_internal_api
from app.utils.logging import get_logger

log = get_logger(__name__)

router = APIRouter(prefix="/api/lead", tags=["lead"])


async def _check_rate_limit(
    x_internal_token: str | None = Header(default=None, alias="X-Internal-Token"),
) -> None:
    """Rate-limit dependency. Runs after verify_internal_token (which validates token).
    By the time we reach here, token is non-empty and valid.
    """
    if not x_internal_token:
        return  # verify_internal_token already raised; defensive
    fp = fingerprint_token(x_internal_token)
    allowed = await can_call_internal_api(fp)
    if not allowed:
        log.warning("internal_api_rate_limited", fingerprint=fp)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Rate limit exceeded — 60 requests per minute",
        )


@router.get(
    "/{short_id}",
    response_model=LeadResponse,
    dependencies=[Depends(verify_internal_token), Depends(_check_rate_limit)],
)
async def get_lead(short_id: str) -> LeadResponse:
    """Return lead context for bot_purify.

    Returns 404 if short_id is not found or the user was soft-deleted.
    Returns 401 if X-Internal-Token is missing or invalid.
    Returns 429 if rate limit exceeded.
    """
    user = await users_repo.get_by_short_id(short_id)
    if user is None:
        log.info("lead_not_found", short_id=short_id)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Lead not found",
        )

    # Resolve scenario_slug from last outgoing message's scenario metadata
    scenario_slug = await _resolve_scenario_slug(user["id"])

    # Recent messages (chronological, last 10)
    msg_rows = await messages_repo.get_recent_for_user(user["id"], limit=10)
    recent = [
        LeadMessage(
            direction=row["direction"],
            text=row["text"],
            created_at=row["created_at"],
        )
        for row in msg_rows
    ]

    response = LeadResponse(
        user=LeadUserInfo(
            platform=user["platform"],
            username=user["username"],
            full_name=user["full_name"],
            first_seen_at=user["first_seen_at"],
        ),
        scenario=scenario_slug,
        recent_messages=recent,
    )

    log.info(
        "lead_fetched",
        short_id=short_id,
        user_id=user["id"],
        message_count=len(recent),
        scenario=scenario_slug,
    )

    return response


@router.post(
    "/{short_id}/handover",
    response_model=HandoverResponse,
    dependencies=[Depends(verify_internal_token), Depends(_check_rate_limit)],
)
async def post_handover(short_id: str, body: HandoverRequest) -> HandoverResponse:
    """Record that the lead has successfully landed in bot_purify.

    Sets social_users.tg_handover_at and social_users.tg_user_id.
    Idempotent: calling again with same tg_user_id is a no-op.
    Returns 404 if short_id not found.
    """
    user = await users_repo.get_by_short_id(short_id)
    if user is None:
        log.info("handover_lead_not_found", short_id=short_id)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Lead not found",
        )

    await lead_tracker.record_handover(
        user_id=user["id"],
        tg_user_id=body.tg_user_id,
    )

    # Re-fetch to get the actual updated timestamp
    updated = await users_repo.get_by_short_id(short_id)
    assert updated is not None  # we just updated this record
    handed_over_at = updated["tg_handover_at"]

    log.info(
        "handover_recorded",
        short_id=short_id,
        user_id=user["id"],
        tg_user_id=body.tg_user_id,
    )

    return HandoverResponse(
        status="ok",
        tg_user_id=body.tg_user_id,
        handed_over_at=handed_over_at,
    )


async def _resolve_scenario_slug(user_id: int) -> str:
    """Extract scenario_slug from the most recent outgoing message's scenario metadata.

    Falls back to 'unknown' if:
    - No outgoing messages exist yet (user just sent first DM, no reply yet)
    - Last out-message has no scenario_id (e.g. echo fallback in early dev)
    - Scenario metadata doesn't contain tg_scenario_slug
    """
    row = await users_repo.get_last_outgoing_with_scenario(user_id)
    if row is None:
        return "unknown"
    metadata = row["scenario_metadata"]
    if not metadata:
        return "unknown"
    # asyncpg returns JSONB as dict
    if isinstance(metadata, dict):
        return metadata.get("tg_scenario_slug", "unknown")
    return "unknown"
```

### 7. Подключение роутера

a) Обновить `app/main.py`:

```python
from app.api import health, lead, webhooks  # add `lead`

# ...

def create_app() -> FastAPI:
    app = FastAPI(
        title="social_inbox",
        version="0.1.0",
        description="Automated messaging service for Instagram/Facebook lead capture",
        lifespan=lifespan,
    )
    app.include_router(health.router)
    app.include_router(webhooks.router)
    app.include_router(lead.router)  # new
    return app
```

### 8. Тесты

a) Создать `tests/test_api_lead.py`:

```python
"""Tests for /api/lead/{short_id} endpoint."""
from __future__ import annotations

import os
from datetime import UTC, datetime

import pytest
from httpx import AsyncClient

from app.repos import conversations, messages, scenarios as scenarios_repo, users
from app.repos.redis_client import get_redis


# Tests assume INTERNAL_API_TOKEN='test-token' (set in conftest.py).
VALID_TOKEN = "test-token"
INVALID_TOKEN = "wrong-token"


async def _make_user_with_history(
    db,
    external_id: str = "lead_user_1",
    full_name: str = "Маша Петрова",
    username: str = "masha_p",
    *,
    with_outgoing: bool = True,
):
    """Create a user with one incoming + optional outgoing message tied to default_welcome."""
    user = await users.create(
        provider_name="sendpulse",
        platform="instagram",
        external_id=external_id,
        username=username,
        full_name=full_name,
    )
    conv = await conversations.create(user["id"], "instagram")

    # Incoming message
    await messages.insert(
        conversation_id=conv["id"],
        direction="in",
        text="Привет",
        external_message_id=f"in_{external_id}",
    )

    if with_outgoing:
        scenario = await scenarios_repo.get_by_name("default_welcome")
        assert scenario is not None
        await messages.insert(
            conversation_id=conv["id"],
            direction="out",
            text=f"Welcome with deep-link to {user['short_id']}",
            scenario_id=scenario["id"],
            external_message_id=f"out_{external_id}",
        )

    return user


@pytest.fixture(autouse=True)
async def _clear_rate_limit() -> None:
    """Clear API rate-limit keys between tests to avoid 429 from neighbours."""
    redis = await get_redis()
    keys = await redis.keys("rl:api:*")
    if keys:
        await redis.delete(*keys)
    yield


@pytest.mark.asyncio
async def test_get_lead_returns_full_context(client: AsyncClient, db) -> None:
    user = await _make_user_with_history(db, "lead_full_1")

    response = await client.get(
        f"/api/lead/{user['short_id']}",
        headers={"X-Internal-Token": VALID_TOKEN},
    )
    assert response.status_code == 200

    body = response.json()
    assert body["user"]["platform"] == "instagram"
    assert body["user"]["username"] == "masha_p"
    assert body["user"]["full_name"] == "Маша Петрова"
    assert body["scenario"] == "purify"  # from default_welcome metadata
    assert len(body["recent_messages"]) == 2
    assert body["recent_messages"][0]["direction"] == "in"
    assert body["recent_messages"][1]["direction"] == "out"


@pytest.mark.asyncio
async def test_get_lead_unknown_scenario_when_no_outgoing(client: AsyncClient, db) -> None:
    user = await _make_user_with_history(db, "lead_no_out", with_outgoing=False)

    response = await client.get(
        f"/api/lead/{user['short_id']}",
        headers={"X-Internal-Token": VALID_TOKEN},
    )
    assert response.status_code == 200
    assert response.json()["scenario"] == "unknown"


@pytest.mark.asyncio
async def test_get_lead_returns_401_without_token(client: AsyncClient, db) -> None:
    user = await _make_user_with_history(db, "lead_no_token")

    response = await client.get(f"/api/lead/{user['short_id']}")
    assert response.status_code == 401
    assert "Missing X-Internal-Token" in response.json()["detail"]


@pytest.mark.asyncio
async def test_get_lead_returns_401_with_wrong_token(client: AsyncClient, db) -> None:
    user = await _make_user_with_history(db, "lead_bad_token")

    response = await client.get(
        f"/api/lead/{user['short_id']}",
        headers={"X-Internal-Token": INVALID_TOKEN},
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_get_lead_returns_404_for_unknown_short_id(client: AsyncClient) -> None:
    response = await client.get(
        "/api/lead/nonexistent",
        headers={"X-Internal-Token": VALID_TOKEN},
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_get_lead_returns_404_for_soft_deleted_user(client: AsyncClient, db) -> None:
    user = await _make_user_with_history(db, "lead_deleted")
    await users.soft_delete(user["id"], datetime.now(UTC))

    response = await client.get(
        f"/api/lead/{user['short_id']}",
        headers={"X-Internal-Token": VALID_TOKEN},
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_get_lead_messages_chronological_order(client: AsyncClient, db) -> None:
    user = await users.create(
        provider_name="sendpulse",
        platform="instagram",
        external_id="lead_order",
    )
    conv = await conversations.create(user["id"], "instagram")

    # Insert 3 messages
    for i in range(3):
        await messages.insert(
            conversation_id=conv["id"],
            direction="in" if i % 2 == 0 else "out",
            text=f"msg-{i}",
            external_message_id=f"order_{i}",
        )

    response = await client.get(
        f"/api/lead/{user['short_id']}",
        headers={"X-Internal-Token": VALID_TOKEN},
    )
    msgs = response.json()["recent_messages"]
    assert len(msgs) == 3
    # Oldest first: msg-0, msg-1, msg-2
    texts = [m["text"] for m in msgs]
    assert texts == ["msg-0", "msg-1", "msg-2"]


@pytest.mark.asyncio
async def test_get_lead_limits_to_10_messages(client: AsyncClient, db) -> None:
    user = await users.create(
        provider_name="sendpulse",
        platform="instagram",
        external_id="lead_limit",
    )
    conv = await conversations.create(user["id"], "instagram")

    for i in range(15):
        await messages.insert(
            conversation_id=conv["id"],
            direction="in",
            text=f"msg-{i:02d}",
            external_message_id=f"limit_{i:02d}",
        )

    response = await client.get(
        f"/api/lead/{user['short_id']}",
        headers={"X-Internal-Token": VALID_TOKEN},
    )
    msgs = response.json()["recent_messages"]
    assert len(msgs) == 10
    # We expect the LAST 10 in chronological order: msg-05 .. msg-14
    assert msgs[0]["text"] == "msg-05"
    assert msgs[-1]["text"] == "msg-14"


@pytest.mark.asyncio
async def test_get_lead_response_schema_matches_contract(client: AsyncClient, db) -> None:
    """Sanity: response keys exactly match the contract documented in CLAUDE.md § 9.2."""
    user = await _make_user_with_history(db, "lead_schema")

    response = await client.get(
        f"/api/lead/{user['short_id']}",
        headers={"X-Internal-Token": VALID_TOKEN},
    )
    body = response.json()
    assert set(body.keys()) == {"user", "scenario", "recent_messages"}
    assert set(body["user"].keys()) == {"platform", "username", "full_name", "first_seen_at"}
    if body["recent_messages"]:
        assert set(body["recent_messages"][0].keys()) == {"direction", "text", "created_at"}


@pytest.mark.asyncio
async def test_handover_records_tg_user_id(client: AsyncClient, db) -> None:
    user = await _make_user_with_history(db, "handover_user_1")

    response = await client.post(
        f"/api/lead/{user['short_id']}/handover",
        json={"tg_user_id": 123456789},
        headers={"X-Internal-Token": VALID_TOKEN},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["tg_user_id"] == 123456789
    assert "handed_over_at" in body

    # Verify DB
    updated = await db.fetchrow(
        "SELECT tg_user_id, tg_handover_at FROM social_users WHERE id = $1",
        user["id"],
    )
    assert updated["tg_user_id"] == 123456789
    assert updated["tg_handover_at"] is not None


@pytest.mark.asyncio
async def test_handover_idempotent(client: AsyncClient, db) -> None:
    user = await _make_user_with_history(db, "handover_idem")

    # First call
    r1 = await client.post(
        f"/api/lead/{user['short_id']}/handover",
        json={"tg_user_id": 111},
        headers={"X-Internal-Token": VALID_TOKEN},
    )
    assert r1.status_code == 200

    # Second call with same tg_user_id — overwrites timestamp, returns 200
    r2 = await client.post(
        f"/api/lead/{user['short_id']}/handover",
        json={"tg_user_id": 111},
        headers={"X-Internal-Token": VALID_TOKEN},
    )
    assert r2.status_code == 200


@pytest.mark.asyncio
async def test_handover_404_for_unknown_short_id(client: AsyncClient) -> None:
    response = await client.post(
        "/api/lead/nonexistent/handover",
        json={"tg_user_id": 999},
        headers={"X-Internal-Token": VALID_TOKEN},
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_handover_validates_positive_tg_user_id(client: AsyncClient, db) -> None:
    user = await _make_user_with_history(db, "handover_invalid")

    response = await client.post(
        f"/api/lead/{user['short_id']}/handover",
        json={"tg_user_id": 0},  # invalid: must be > 0
        headers={"X-Internal-Token": VALID_TOKEN},
    )
    assert response.status_code == 422  # Pydantic validation


@pytest.mark.asyncio
async def test_handover_rejects_extra_fields(client: AsyncClient, db) -> None:
    user = await _make_user_with_history(db, "handover_extra")

    response = await client.post(
        f"/api/lead/{user['short_id']}/handover",
        json={"tg_user_id": 123, "secret_field": "x"},
        headers={"X-Internal-Token": VALID_TOKEN},
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_rate_limit_exceeded_returns_429(client: AsyncClient, db) -> None:
    user = await _make_user_with_history(db, "rate_limit_user")

    # Exhaust the limit (60 RPM)
    for _ in range(60):
        r = await client.get(
            f"/api/lead/{user['short_id']}",
            headers={"X-Internal-Token": VALID_TOKEN},
        )
        assert r.status_code == 200

    # 61st should be throttled
    r_throttled = await client.get(
        f"/api/lead/{user['short_id']}",
        headers={"X-Internal-Token": VALID_TOKEN},
    )
    assert r_throttled.status_code == 429
```

b) Создать `tests/test_e2e_full_funnel.py`:

```python
"""Most important test in the project: full funnel end-to-end.

Pipeline:
1. User comments "ОЧИЩЕНИЕ" under a Reels (event_type='comment')
2. Webhook → events_log → worker
3. comment-to-DM scenario fires → produces DM with deep-link
4. FakeProvider receives the DM
5. Extract short_id from the DM text (simulates user clicking the link)
6. GET /api/lead/{short_id} → bot_purify-style response
7. POST /api/lead/{short_id}/handover → record the conversion
8. Verify tg_handover_at and tg_user_id are set in DB

If this test passes, the entire social_inbox→bot_purify integration is wired correctly.
"""
from __future__ import annotations

import re
from datetime import UTC, datetime

import pytest
from httpx import AsyncClient

from app.models.events import IncomingEvent
from app.repos import events as events_repo, users
from app.workers.tasks_messages import process_incoming_event
from tests.fakes.fake_provider import FakeProvider

VALID_TOKEN = "test-token"


@pytest.mark.asyncio
async def test_full_funnel_comment_to_telegram_handover(
    client: AsyncClient,
    fake_provider: FakeProvider,
    db,
) -> None:
    # Step 1: User comments with keyword
    event = IncomingEvent(
        provider="sendpulse",
        platform="instagram",
        event_type="comment",
        external_user_id="funnel_user_1",
        external_event_id="funnel_evt_1",
        username="anna_purify",
        full_name="Anna Purifier",
        text="хочу ОЧИЩЕНИЕ программу!",
        post_id="reels_42",
        comment_id="comment_42",
        occurred_at=datetime(2026, 4, 30, 10, 0, tzinfo=UTC),
    )
    fake_provider.queue_event(event)

    # Step 2: Webhook
    response = await client.post("/webhooks/sendpulse", json={})
    assert response.status_code == 200

    log_row = await db.fetchrow(
        "SELECT * FROM events_log WHERE external_event_id = 'funnel_evt_1'",
    )

    # Step 3-4: Worker processes; FakeProvider receives outgoing
    await process_incoming_event({}, event.model_dump(mode="json"), log_row["id"])
    assert len(fake_provider.sent) == 1
    sent_dm = fake_provider.sent[0]

    # Step 5: Extract short_id from deep-link in the DM text
    # Pattern: ig_<short_id>_<scenario_slug>
    match = re.search(r"ig_([0-9A-Za-z]{8})_(\w+)", sent_dm.text or "")
    assert match is not None, f"No deep-link found in: {sent_dm.text}"
    short_id, slug_in_link = match.groups()

    # Step 6: bot_purify dereferences the deep-link
    lead_response = await client.get(
        f"/api/lead/{short_id}",
        headers={"X-Internal-Token": VALID_TOKEN},
    )
    assert lead_response.status_code == 200
    lead_body = lead_response.json()
    assert lead_body["user"]["full_name"] == "Anna Purifier"
    assert lead_body["scenario"] == slug_in_link  # 'purify' for default_purify_comment
    assert len(lead_body["recent_messages"]) >= 2  # incoming comment + outgoing DM

    # Step 7: bot_purify confirms successful landing
    handover_response = await client.post(
        f"/api/lead/{short_id}/handover",
        json={"tg_user_id": 555111222},
        headers={"X-Internal-Token": VALID_TOKEN},
    )
    assert handover_response.status_code == 200

    # Step 8: Verify DB state — full conversion recorded
    user = await users.get_by_external("sendpulse", "instagram", "funnel_user_1")
    assert user is not None
    assert user["tg_user_id"] == 555111222
    assert user["tg_handover_at"] is not None
```

### 9. CLAUDE.md обновление

a) Найти § 17 (roadmap) и отметить Task 11 как реализованный (если используешь чек-боксы).

b) (Опционально) Добавить в § 9.2 ссылку на код:

```markdown
**Реализация:** `app/api/lead.py`. Pydantic-модели контракта:
`app/models/lead.py` (LeadResponse, LeadUserInfo, LeadMessage, HandoverRequest,
HandoverResponse). Любое изменение моделей требует синхронного обновления
`bot_purify/services/social_inbox.py:LeadContext`.
```

---

## Acceptance criteria

После выполнения всех подзадач выполнить и поставить галочки:

- [ ] Файлы созданы по структуре подзадач 1–7
- [ ] `make lint` проходит без ошибок
- [ ] `make test` проходит, все новые тесты зелёные:
  - `test_api_lead.py` — 14 тестов
  - `test_e2e_full_funnel.py` — 1 ключевой тест (главный артефакт задачи)
  - Все существующие тесты Tasks 01, 03, 04, 06, 07, 08, 09 продолжают работать
- [ ] Ручная проверка через curl (после `make up`):
  ```bash
  # 401 без токена
  curl -i http://localhost:8000/api/lead/abcdefgh
  # → 401 Missing X-Internal-Token

  # 401 с неверным токеном
  curl -i -H "X-Internal-Token: wrong" http://localhost:8000/api/lead/abcdefgh
  # → 401 Invalid X-Internal-Token

  # 404 на неизвестный short_id
  curl -i -H "X-Internal-Token: $(grep INTERNAL_API_TOKEN .env | cut -d= -f2)" \
       http://localhost:8000/api/lead/nonexistent
  # → 404 Lead not found
  ```
- [ ] OpenAPI документация показывает новые endpoint'ы:
  - Открыть http://localhost:8000/docs
  - Видны разделы `/api/lead/{short_id}` GET и `/api/lead/{short_id}/handover` POST
  - Можно нажать «Try it out», вписать `X-Internal-Token` в `Authorize`
- [ ] **Главное:** `test_e2e_full_funnel.py` зелёный — это значит, что воронка работает целиком от комментария в IG до записи о handover в БД

---

## Do NOT

- НЕ возвращать в response поля internal-state: `id`, `metadata`, `claude_tokens`, `raw_payload`, `external_id`, `provider_name`. Контракт минимальный.
- НЕ добавлять GET без X-Internal-Token. Никаких public endpoint'ов в `/api/`.
- НЕ хранить токен в логах или Redis-ключах в plain text. Только fingerprint (SHA-256 первых 16 hex).
- НЕ возвращать список conversations отдельно от messages. В этой задаче API минималистичный.
- НЕ менять формат deep-link или scenario_slug. Они зафиксированы в Task 08.
- НЕ добавлять GET-вариант `/handover`. Запись данных — только POST. RFC.
- НЕ возвращать сообщения от soft-deleted пользователей. `get_by_short_id` уже фильтрует.
- НЕ добавлять CSRF, CORS — это internal API между двумя нашими сервисами в одной Docker сети.
- НЕ повышать лимит recent_messages выше 10 без обсуждения. Bot_purify не должен делать долгие запросы при /start.
- НЕ возвращать `error: ...` структурированно — только `detail` (FastAPI default). bot_purify работает с HTTP-кодом, не с телом ошибки.
- НЕ забыть про `extra="forbid"` в Pydantic-моделях. Это защищает от случайного включения новых полей в API без code-review.

---

## Зависимости задачи

- Tasks 01, 03, 04, 06, 07, 08, 09 применены
- Не требует Task 05 (SendPulseProvider) — все тесты идут через FakeProvider

---

## Что после этой задачи

После применения у тебя есть **полностью работающая воронка end-to-end** через FakeProvider:

```
Comment "ОЧИЩЕНИЕ" → webhook → DB → comment-to-DM scenario →
deep-link DM → /api/lead/{short_id} → bot_purify gets context →
quiz → handover recorded
```

Это **главный milestone проекта**. Можно показывать Юле — даже без реального SendPulse, через мок-демонстрацию ясно, что вся логика работает.

Дальше:

- **Task 05** — SendPulseProvider (нужны Юлины credentials — вы как раз сейчас этим занимаетесь)
- **Task 13** — Claude integration: умные ответы на FAQ. Сейчас echo fallback на не-приветственные сообщения. С Claude получим осмысленные ответы.
- **Task 14** — Safety filters + handover scenario для doTERRA-compliance
- **Task 15-18** — admin dashboard, monitoring, deploy, go-live

---

**Дата создания:** 2026-05-08
**Применять в:** `D:\Work\social_inbox` после Tasks 01, 03, 04, 06, 07, 08, 09
**Эстимейт:** 3–4 часа на Claude Code + ручная проверка
