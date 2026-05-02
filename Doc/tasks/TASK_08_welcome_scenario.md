# Task 08: Welcome scenario + lead_tracker

> Применить в `D:\Work\social_inbox` после Tasks 01, 03, 04, 06, 07. Положить файл в корень проекта, открыть в VS Code, попросить Claude Code: «Прочитай TASK_08_welcome_scenario.md и выполни все подзадачи последовательно. Перед каждой подзадачей кратко опиши план, после — что фактически сделано».

---

## Контекст

После Task 07 у нас работает echo-сценарий — бот отвечает «Получено: <текст>» на любой вход. Это инженерный stub.

Теперь делаем **первый реальный сценарий**: welcome — приветственное сообщение при первом контакте пользователя. Welcome не просто здоровается — он отправляет deep-link в Telegram-бот `@yuliya_purify_bot`, чтобы лид перешёл в воронку прогрева.

Цепочка с точки зрения подписчика:
1. Подписчик пишет в IG DM «Привет» (или комментирует Reels со словом «ОЧИЩЕНИЕ»)
2. Получает welcome-сообщение от Юли с двумя кнопками: «Перейти в Telegram» и «Узнать больше»
3. Жмёт кнопку → Telegram открывает `t.me/yuliya_purify_bot?start=ig_<short_id>_purify`
4. bot_purify видит deep-link, дёргает `GET /api/lead/<short_id>` (Task 11), забирает контекст
5. Дальше квиз в bot_purify, прогрев, продажа программы

В этой задаче мы делаем шаги 1–3. Шаг 4 — Task 11. Шаг 5 — `bot_purify` (уже работает после применения `TASK_social_inbox_integration.md`).

**Важное архитектурное решение:** lead_tracker логически был отдельной Task 10, но welcome без deep-link бессмыслен. Объединяем их в Task 08.

---

## Цель

После выполнения этой задачи:

- Существует `app/services/lead_tracker.py` со всеми функциями работы с deep-link и handover-отметкой
- Существует `app/services/scenarios/welcome.py` с зарегистрированным `WelcomeScenario` (тип `'welcome'`)
- В БД есть seeded welcome-сценарий с реалистичным шаблоном текста
- `scenarios.metadata` JSONB колонка добавлена через миграцию 006
- Welcome отправляется **ровно один раз** на пользователя (idempotency через Redis-флаг)
- Quick replies в welcome корректно сериализуются для будущей отправки через SendPulse
- `make test` зелёный, новые тесты покрывают: deep-link генерацию, lifetime-idempotency, шаблон-подстановку, e2e через FakeProvider
- CLAUDE.md обновлён — добавлен раздел про lead_tracker и формат deep-link

---

## Подзадачи

### 1. Миграция 006 — scenarios.metadata

a) Создать `migrations/006_scenarios_metadata.sql`:

```sql
-- Migration 006: scenarios.metadata for flexible per-scenario configuration.
--
-- Used by:
-- - welcome scenario: stores tg_scenario_slug for deep-link payload
-- - future scenarios: any non-schema config (e.g. claude tool list, A/B variant)
-- Avoids ALTER TABLE for each new optional config field.

ALTER TABLE scenarios
    ADD COLUMN IF NOT EXISTS metadata JSONB NOT NULL DEFAULT '{}'::jsonb;
```

### 2. Миграция 007 — seed welcome scenario

a) Создать `migrations/007_seed_welcome_scenario.sql`:

```sql
-- Migration 007: Seed default welcome scenario.
--
-- This scenario is triggered by ScenarioEngine when a brand-new user sends
-- their first DM and there's no keyword match.
--
-- Template uses placeholders resolved at runtime by welcome.py:
--   {first_name}  — user's first name (or 'дорогая' if missing)
--   {tg_link}     — full deep-link URL to @yuliya_purify_bot
--   {disclaimer}  — AI-assistant disclaimer (Meta + legal compliance)

INSERT INTO scenarios (name, type, template, metadata, active)
VALUES (
    'default_welcome',
    'welcome',
    E'🌿 Привет, {first_name}!\n\nРада видеть тебя здесь 💚 Я — Юлия, консультант doTERRA. Помогаю женщинам перейти на здоровый образ жизни через эфирные масла и программу «Очищение».\n\nЧтобы я могла рассказать тебе подробнее и подобрать что подойдёт именно тебе, переходи в Telegram — там удобнее общаться:\n\n👉 {tg_link}\n\n{disclaimer}',
    '{"tg_scenario_slug": "purify", "quick_replies": [{"title": "Перейти в Telegram", "type": "url", "payload": "{tg_link}"}, {"title": "Узнать больше", "type": "postback", "payload": "more_info"}]}'::jsonb,
    TRUE
)
ON CONFLICT (name) DO NOTHING;
```

   **Замечание:** quick_replies хранятся в `metadata`, а не в существующей колонке `scenarios.quick_replies` (она тоже есть, но её формат `[{title, payload}]` не различает url/postback). Welcome рендерит их сам через `lead_tracker`. В будущем при унификации scenarios.quick_replies и metadata.quick_replies — отдельная задача.

### 3. Конфигурация — добавить telegram_bot_username

a) В `app/config.py` уже есть поле `telegram_bot_username`. Убедиться, что в `.env.example` оно стоит:

```bash
TELEGRAM_BOT_USERNAME=yuliya_purify_bot
```

b) Если в `.env` локально нет — добавить.

### 4. Lead tracker

a) Создать `app/services/lead_tracker.py`:

```python
"""Service for tracking the journey from social DM to Telegram bot.

Responsibilities:
- Build Telegram deep-link URLs in the format:
    https://t.me/{bot_username}?start=ig_{short_id}_{scenario_slug}
- Mark welcome-sent flag in Redis (lifetime idempotency)
- Record handover when bot_purify confirms the lead arrived (Task 11)

Deep-link payload format:
    ig_<short_id>_<scenario_slug>

Where:
- 'ig'           - prefix indicating Instagram/social_inbox origin
                   (matches the parser in bot_purify/handlers/start.py)
- short_id       - 8 chars alphanumeric, no '_' or '-' (see app/utils/short_id.py)
- scenario_slug  - lowercase ASCII identifier, no '_' inside
                   (e.g. 'purify', 'oils', 'faq')

Why no '_' inside scenario_slug:
    bot_purify parser splits payload on FIRST underscore after 'ig_':
    "ig_abc123_purify"      -> short_id='abc123', scenario='purify'
    "ig_abc123_purify_v2"   -> short_id='abc123', scenario='purify_v2'  (still works)
    But avoid leading underscores in slug to keep splitting predictable.
"""
from __future__ import annotations

import re
from datetime import UTC, datetime

from app.config import get_settings
from app.repos import users
from app.repos.redis_client import get_redis
from app.utils.logging import get_logger

log = get_logger(__name__)

# Welcome-sent flag TTL — long enough that lifetime-once is effectively guaranteed,
# but bounded so Redis isn't infinitely growing. 180 days = 6 months > typical user
# lifecycle in our funnel. After expiry, a user re-engaging would get welcome again,
# which is acceptable.
WELCOME_TTL_SECONDS = 60 * 60 * 24 * 180

_SLUG_RE = re.compile(r"^[a-z0-9]+(?:[-][a-z0-9]+)*$")


def _validate_slug(slug: str) -> None:
    """Raise ValueError if slug contains forbidden characters."""
    if not _SLUG_RE.match(slug):
        raise ValueError(
            f"Invalid scenario_slug {slug!r}: must be lowercase alphanumeric, "
            f"optional '-' separator, no '_' allowed"
        )


def build_deep_link(short_id: str, scenario_slug: str = "purify") -> str:
    """Build the full Telegram deep-link URL.

    Examples:
        build_deep_link('Kd7nQ2x9')                  -> https://t.me/yuliya_purify_bot?start=ig_Kd7nQ2x9_purify
        build_deep_link('Kd7nQ2x9', 'oils')          -> https://t.me/yuliya_purify_bot?start=ig_Kd7nQ2x9_oils
    """
    _validate_slug(scenario_slug)
    settings = get_settings()
    return (
        f"https://t.me/{settings.telegram_bot_username}"
        f"?start=ig_{short_id}_{scenario_slug}"
    )


# ---- Welcome lifetime idempotency ----

def _welcome_key(user_id: int) -> str:
    return f"welcome:sent:{user_id}"


async def was_welcome_sent(user_id: int) -> bool:
    """Return True if welcome was already sent to this user within TTL."""
    redis = await get_redis()
    return bool(await redis.exists(_welcome_key(user_id)))


async def mark_welcome_sent(user_id: int) -> None:
    """Set the welcome-sent flag with a 180-day TTL."""
    redis = await get_redis()
    await redis.set(
        _welcome_key(user_id),
        datetime.now(UTC).isoformat(),
        ex=WELCOME_TTL_SECONDS,
    )


# ---- Handover recording (used by Task 11) ----

async def record_handover(user_id: int, tg_user_id: int) -> None:
    """Record that the user successfully landed in the Telegram bot."""
    await users.mark_handover(user_id, tg_user_id, datetime.now(UTC))
    log.info("handover_recorded", user_id=user_id, tg_user_id=tg_user_id)
```

### 5. Welcome сценарий

a) Создать `app/services/scenarios/welcome.py`:

```python
"""Welcome scenario — first contact greeting with Telegram deep-link.

Triggered by ScenarioEngine when a brand-new user sends their first DM
(or when a keyword explicitly maps to a welcome-typed scenario).

Behavior:
- Look up user's short_id (must already exist — created by worker before engine call)
- Build deep-link URL using scenario.metadata.tg_scenario_slug
- Resolve {first_name}, {tg_link}, {disclaimer} placeholders in template
- Build quick_replies from scenario.metadata.quick_replies
- Mark welcome-sent flag in Redis to prevent re-sending

Lifetime guarantee:
- Each user receives welcome at most once per WELCOME_TTL_SECONDS (180 days)
- If welcome was already sent, this scenario returns None (silent skip)
"""
from __future__ import annotations

from typing import Any

import asyncpg

from app.models.events import IncomingEvent, OutgoingMessage, QuickReply
from app.services import lead_tracker
from app.services.scenario_engine import register_scenario
from app.utils.logging import get_logger

log = get_logger(__name__)

# Disclaimer text — required by:
# - Meta App Review (when we eventually migrate to direct Meta integration)
# - doTERRA compliance (clarifies it's an automated assistant, not Yulia personally)
# - User trust (users dislike covert automation)
DISCLAIMER = (
    "ℹ️ Это автоматический помощник. "
    "Чтобы написать Юле напрямую — напиши в ответ слово «оператор»."
)

DEFAULT_FIRST_NAME = "дорогая"


@register_scenario("welcome")
async def handle_welcome(
    event: IncomingEvent,
    user: asyncpg.Record,
    conversation: asyncpg.Record,
    scenario: asyncpg.Record,
) -> OutgoingMessage | None:
    # 1. Lifetime idempotency
    if await lead_tracker.was_welcome_sent(user["id"]):
        log.info(
            "welcome_skipped_already_sent",
            user_id=user["id"],
            short_id=user["short_id"],
        )
        return None

    # 2. Resolve scenario_slug from metadata (default 'purify')
    metadata = dict(scenario["metadata"]) if scenario["metadata"] else {}
    scenario_slug = metadata.get("tg_scenario_slug", "purify")

    # 3. Build deep-link
    try:
        tg_link = lead_tracker.build_deep_link(user["short_id"], scenario_slug)
    except ValueError as exc:
        log.error(
            "welcome_invalid_slug",
            scenario_id=scenario["id"],
            slug=scenario_slug,
            error=str(exc),
        )
        return None

    # 4. Resolve placeholders in template
    first_name = _extract_first_name(user["full_name"]) or DEFAULT_FIRST_NAME
    template = scenario["template"] or ""
    text = template.format(
        first_name=first_name,
        tg_link=tg_link,
        disclaimer=DISCLAIMER,
    )

    # 5. Build quick_replies from metadata (resolve {tg_link} placeholder)
    quick_replies = _build_quick_replies(metadata, tg_link)

    # 6. Mark welcome-sent BEFORE returning the message
    # (worker will send it; we want idempotency even if send fails — better to
    # not double-send on retry than to retry forever and spam.)
    await lead_tracker.mark_welcome_sent(user["id"])

    log.info(
        "welcome_built",
        user_id=user["id"],
        short_id=user["short_id"],
        slug=scenario_slug,
    )

    return OutgoingMessage(
        platform=event.platform,
        external_user_id=event.external_user_id,
        text=text,
        quick_replies=quick_replies,
        scenario_id=scenario["id"],
    )


def _extract_first_name(full_name: str | None) -> str | None:
    """Take first whitespace-separated word from full_name. Returns None if empty."""
    if not full_name:
        return None
    parts = full_name.strip().split()
    return parts[0] if parts else None


def _build_quick_replies(
    metadata: dict[str, Any],
    tg_link: str,
) -> list[QuickReply] | None:
    """Build QuickReply list from scenario metadata, resolving {tg_link} placeholder."""
    raw = metadata.get("quick_replies")
    if not raw or not isinstance(raw, list):
        return None

    out: list[QuickReply] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        title = item.get("title")
        payload = item.get("payload")
        if not title or not payload:
            continue
        # Replace {tg_link} placeholder if present
        resolved_payload = payload.replace("{tg_link}", tg_link)
        try:
            out.append(QuickReply(title=title, payload=resolved_payload))
        except Exception as exc:
            log.warning(
                "quick_reply_invalid",
                title=title,
                payload=payload,
                error=str(exc),
            )
            continue

    return out or None
```

### 6. Регистрация в __init__

a) Обновить `app/services/scenarios/__init__.py` — добавить импорт welcome:

```python
"""Scenario implementations.

Each scenario lives in its own module. Importing this package
triggers handler registration via the @register_scenario decorator.
"""
from app.services.scenarios import echo  # noqa: F401 — side effect only
from app.services.scenarios import welcome  # noqa: F401 — side effect only

__all__ = ["echo", "welcome"]
```

### 7. CLAUDE.md обновление

a) В `CLAUDE.md` найти § 10.1 «Поток» (раздел про интеграцию с bot_purify) и в шаге 5 явно зафиксировать формат deep-link:

   Найти:
   ```
   5. lead_tracker генерирует short_id = "Kd7nQ2x9", сохраняет в БД
   ```

   Добавить пояснительный блок сразу после § 10:

```markdown
### 10.5. Формат deep-link

Зафиксированный формат deep-link (используется `lead_tracker.build_deep_link`):

    https://t.me/{telegram_bot_username}?start=ig_{short_id}_{scenario_slug}

Правила:
- `short_id` — 8 chars `[0-9A-Za-z]`, без `_` и `-` (см. `app/utils/short_id.py`)
- `scenario_slug` — lowercase ASCII `[a-z0-9-]+`, без `_` и пробелов
- bot_purify парсит payload по первому `_` после префикса `ig_` →
  `(short_id, scenario_slug)`. См. `bot_purify/handlers/start.py:_parse_deep_link`.
- Известные слаги: `purify`, `oils`, `faq`. Новые слаги добавляются по согласованию
  с командой bot_purify (могут понадобиться отдельные приветствия).

### 10.6. lead_tracker сервис

`app/services/lead_tracker.py` — единая точка работы с переходом «social → Telegram»:

- `build_deep_link(short_id, slug)` — формирует URL по правилам §10.5
- `was_welcome_sent(user_id)` / `mark_welcome_sent(user_id)` — Redis-флаги
  lifetime-idempotency для welcome (TTL 180 дней)
- `record_handover(user_id, tg_user_id)` — отметка успешного перехода
  (вызывается из endpoint'а в Task 11)
```

### 8. Тесты

a) Создать `tests/test_lead_tracker.py`:

```python
"""Tests for lead_tracker service."""
from __future__ import annotations

import pytest

from app.services import lead_tracker
from app.repos.redis_client import get_redis


def test_build_deep_link_default_slug() -> None:
    url = lead_tracker.build_deep_link("Kd7nQ2x9")
    assert url == "https://t.me/yuliya_purify_bot?start=ig_Kd7nQ2x9_purify"


def test_build_deep_link_custom_slug() -> None:
    url = lead_tracker.build_deep_link("abc123", "oils")
    assert url == "https://t.me/yuliya_purify_bot?start=ig_abc123_oils"


def test_build_deep_link_rejects_underscore_in_slug() -> None:
    with pytest.raises(ValueError, match="Invalid scenario_slug"):
        lead_tracker.build_deep_link("abc123", "purify_v2")


def test_build_deep_link_rejects_uppercase_in_slug() -> None:
    with pytest.raises(ValueError):
        lead_tracker.build_deep_link("abc123", "Purify")


def test_build_deep_link_accepts_hyphen_in_slug() -> None:
    url = lead_tracker.build_deep_link("abc123", "early-bird")
    assert "ig_abc123_early-bird" in url


@pytest.mark.asyncio
async def test_welcome_flag_lifecycle() -> None:
    redis = await get_redis()
    user_id = 99001
    await redis.delete(lead_tracker._welcome_key(user_id))

    assert await lead_tracker.was_welcome_sent(user_id) is False
    await lead_tracker.mark_welcome_sent(user_id)
    assert await lead_tracker.was_welcome_sent(user_id) is True


@pytest.mark.asyncio
async def test_record_handover_updates_user(db) -> None:
    from app.repos import users

    user = await users.create(
        provider_name="sendpulse",
        platform="instagram",
        external_id="handover_user_1",
    )
    await lead_tracker.record_handover(user["id"], tg_user_id=12345)

    refreshed = await db.fetchrow(
        "SELECT tg_handover_at, tg_user_id FROM social_users WHERE id = $1",
        user["id"],
    )
    assert refreshed["tg_handover_at"] is not None
    assert refreshed["tg_user_id"] == 12345
```

b) Создать `tests/test_scenario_welcome.py`:

```python
"""Tests for welcome scenario handler."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.models.events import IncomingEvent
from app.repos import conversations, scenarios as scenarios_repo, users
from app.repos.redis_client import get_redis
from app.services import lead_tracker
from app.services.scenarios.welcome import handle_welcome


def _make_event(
    text: str = "Привет",
    external_user_id: str = "welcome_user",
) -> IncomingEvent:
    return IncomingEvent(
        provider="sendpulse",
        platform="instagram",
        event_type="message",
        external_user_id=external_user_id,
        external_event_id=f"evt_w_{external_user_id}",
        full_name="Маша Петрова",
        text=text,
        occurred_at=datetime(2026, 4, 30, 12, 0, tzinfo=UTC),
    )


async def _setup(db, external_id: str = "welcome_user"):
    """Create user, conversation, fetch welcome scenario row, clear redis flag."""
    user = await users.create(
        provider_name="sendpulse",
        platform="instagram",
        external_id=external_id,
        full_name="Маша Петрова",
    )
    conv = await conversations.create(user["id"], "instagram")
    scenario = await scenarios_repo.get_by_name("default_welcome")
    assert scenario is not None, "default_welcome should be seeded by migration 007"

    redis = await get_redis()
    await redis.delete(lead_tracker._welcome_key(user["id"]))

    return user, conv, scenario


@pytest.mark.asyncio
async def test_welcome_returns_message_with_deep_link(db) -> None:
    user, conv, scenario = await _setup(db, "welcome_basic")
    event = _make_event(external_user_id="welcome_basic")

    msg = await handle_welcome(event, user, conv, scenario)

    assert msg is not None
    assert msg.text is not None
    assert "Маша" in msg.text  # first_name resolved
    assert f"ig_{user['short_id']}_purify" in msg.text  # deep-link present
    assert "автоматический помощник" in msg.text  # disclaimer present
    assert msg.scenario_id == scenario["id"]


@pytest.mark.asyncio
async def test_welcome_quick_replies_resolved(db) -> None:
    user, conv, scenario = await _setup(db, "welcome_qr")
    event = _make_event(external_user_id="welcome_qr")

    msg = await handle_welcome(event, user, conv, scenario)

    assert msg is not None
    assert msg.quick_replies is not None
    assert len(msg.quick_replies) == 2

    tg_button = msg.quick_replies[0]
    assert tg_button.title == "Перейти в Telegram"
    assert "https://t.me/yuliya_purify_bot" in tg_button.payload
    assert user["short_id"] in tg_button.payload

    info_button = msg.quick_replies[1]
    assert info_button.title == "Узнать больше"
    assert info_button.payload == "more_info"


@pytest.mark.asyncio
async def test_welcome_lifetime_idempotency(db) -> None:
    user, conv, scenario = await _setup(db, "welcome_idem")
    event = _make_event(external_user_id="welcome_idem")

    # First call: returns message
    msg1 = await handle_welcome(event, user, conv, scenario)
    assert msg1 is not None

    # Second call: returns None (already sent)
    msg2 = await handle_welcome(event, user, conv, scenario)
    assert msg2 is None


@pytest.mark.asyncio
async def test_welcome_uses_default_name_for_anonymous_user(db) -> None:
    user = await users.create(
        provider_name="sendpulse",
        platform="instagram",
        external_id="welcome_anon",
        # full_name=None
    )
    conv = await conversations.create(user["id"], "instagram")
    scenario = await scenarios_repo.get_by_name("default_welcome")
    redis = await get_redis()
    await redis.delete(lead_tracker._welcome_key(user["id"]))

    event = _make_event(external_user_id="welcome_anon")
    # Override event.full_name to None
    event_anon = event.model_copy(update={"full_name": None})

    msg = await handle_welcome(event_anon, user, conv, scenario)

    assert msg is not None
    assert msg.text is not None
    assert "дорогая" in msg.text  # default fallback


@pytest.mark.asyncio
async def test_welcome_respects_metadata_slug(db) -> None:
    """A custom welcome scenario with metadata.tg_scenario_slug='oils'."""
    custom = await db.fetchrow(
        """
        INSERT INTO scenarios (name, type, template, metadata, active)
        VALUES (
            'oils_welcome',
            'welcome',
            'Привет, {first_name}! Перейди: {tg_link}\n{disclaimer}',
            '{"tg_scenario_slug": "oils"}'::jsonb,
            TRUE
        )
        RETURNING *
        """
    )
    user = await users.create(
        provider_name="sendpulse",
        platform="instagram",
        external_id="welcome_oils",
        full_name="Анна",
    )
    conv = await conversations.create(user["id"], "instagram")
    redis = await get_redis()
    await redis.delete(lead_tracker._welcome_key(user["id"]))

    event = _make_event(external_user_id="welcome_oils")
    msg = await handle_welcome(event, user, conv, custom)

    assert msg is not None
    assert msg.text is not None
    assert f"ig_{user['short_id']}_oils" in msg.text
```

c) Создать `tests/test_e2e_welcome_pipeline.py`:

```python
"""End-to-end test: new user's first DM triggers welcome with deep-link."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest
from httpx import AsyncClient

from app.models.events import IncomingEvent
from app.repos import events as events_repo, users
from app.repos.redis_client import get_redis
from app.services import lead_tracker
from app.workers.tasks_messages import process_incoming_event
from tests.fakes.fake_provider import FakeProvider


@pytest.mark.asyncio
async def test_new_user_first_dm_triggers_welcome(
    client: AsyncClient,
    fake_provider: FakeProvider,
    db,
) -> None:
    event = IncomingEvent(
        provider="sendpulse",
        platform="instagram",
        event_type="message",
        external_user_id="e2e_welcome_user",
        external_event_id="e2e_welcome_evt_1",
        username="masha_p",
        full_name="Маша Петрова",
        text="Привет",
        occurred_at=datetime(2026, 4, 30, 12, 0, tzinfo=UTC),
    )
    fake_provider.queue_event(event)

    # POST webhook
    response = await client.post("/webhooks/sendpulse", json={})
    assert response.status_code == 200

    log_row = await db.fetchrow(
        "SELECT * FROM events_log WHERE external_event_id = $1",
        "e2e_welcome_evt_1",
    )
    assert log_row is not None

    # Drive worker
    await process_incoming_event({}, event.model_dump(mode="json"), log_row["id"])

    # Verify user created with short_id
    user = await users.get_by_external("sendpulse", "instagram", "e2e_welcome_user")
    assert user is not None
    assert user["short_id"] is not None

    # Verify outgoing welcome message in DB
    msgs = await db.fetch(
        """
        SELECT m.* FROM messages m
        JOIN conversations c ON c.id = m.conversation_id
        WHERE c.user_id = $1 AND m.direction = 'out'
        """,
        user["id"],
    )
    assert len(msgs) == 1
    out_msg = msgs[0]
    assert "Маша" in out_msg["text"]
    assert f"ig_{user['short_id']}_purify" in out_msg["text"]

    # Verify provider received the welcome
    assert len(fake_provider.sent) == 1
    sent = fake_provider.sent[0]
    assert sent.quick_replies is not None
    assert len(sent.quick_replies) == 2

    # Verify welcome flag set in Redis
    assert await lead_tracker.was_welcome_sent(user["id"]) is True


@pytest.mark.asyncio
async def test_returning_user_does_not_get_welcome_again(
    client: AsyncClient,
    fake_provider: FakeProvider,
    db,
) -> None:
    """If welcome was already sent (Redis flag set), second message → echo fallback, not welcome."""
    # Setup: pre-create user and mark welcome as sent
    user = await users.create(
        provider_name="sendpulse",
        platform="instagram",
        external_id="e2e_returning",
        full_name="Anna",
    )
    await lead_tracker.mark_welcome_sent(user["id"])

    event = IncomingEvent(
        provider="sendpulse",
        platform="instagram",
        event_type="message",
        external_user_id="e2e_returning",
        external_event_id="e2e_ret_evt_1",
        username="anna",
        text="Hi again",
        occurred_at=datetime.now(UTC),
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

    # Should NOT receive welcome (was_welcome_sent=True so welcome handler returns None)
    # Engine then falls back to echo (since user is not "new" — already exists in DB)
    msgs = await db.fetch(
        """
        SELECT m.* FROM messages m
        JOIN conversations c ON c.id = m.conversation_id
        WHERE c.user_id = $1 AND m.direction = 'out'
        ORDER BY m.created_at ASC
        """,
        user["id"],
    )
    assert len(msgs) == 1
    # Echo, not welcome — no deep-link in text
    out_text = msgs[0]["text"]
    assert "Получено:" in out_text
    assert "ig_" not in out_text  # no deep-link
```

---

## Acceptance criteria

После выполнения всех подзадач выполнить и поставить галочки:

- [ ] Файлы созданы по структуре подзадач 1–6
- [ ] Миграции 006 и 007 применены: `docker compose exec postgres psql -U social_inbox -d social_inbox -c "SELECT name, type, metadata FROM scenarios WHERE name='default_welcome'"` показывает строку с `tg_scenario_slug` в metadata
- [ ] `make lint` проходит без ошибок
- [ ] `make test` проходит, все новые тесты зелёные:
  - `test_lead_tracker.py` — 7 тестов
  - `test_scenario_welcome.py` — 5 тестов
  - `test_e2e_welcome_pipeline.py` — 2 теста
  - Существующие тесты (Tasks 01, 03, 04, 06, 07) продолжают работать
- [ ] CLAUDE.md обновлён — добавлены §§ 10.5, 10.6
- [ ] Ручная проверка через docker compose:
  ```bash
  docker compose exec api python -c "from app.services.lead_tracker import build_deep_link; print(build_deep_link('TestSid7'))"
  ```
  Выводит: `https://t.me/yuliya_purify_bot?start=ig_TestSid7_purify`
- [ ] При повторном запуске e2e (один и тот же external_user_id) welcome НЕ отправляется второй раз — в логах видно `welcome_skipped_already_sent`
- [ ] Quick replies включают URL и postback кнопки, payload URL-кнопки содержит реальный short_id

---

## Do NOT

- НЕ реализовывать Comment-to-DM сценарий в этой задаче. Это Task 09. Welcome триггерится только при первом DM, не при комментарии.
- НЕ применять safety-фильтры к welcome-тексту. Текст из БД, контролируется админом, доверенный. Safety — Task 14, для исходящих ответов Claude.
- НЕ добавлять Claude API в welcome. Welcome — детерминированный шаблон, без AI.
- НЕ модифицировать `process_incoming_event` в этой задаче. Логика worker'а из Task 07 уже корректно вызывает welcome через ScenarioEngine — ничего менять не нужно.
- НЕ хранить welcome-flag в БД. Только Redis. Reasoning: эфемерный, expirable, идиоматический use case Redis.
- НЕ добавлять scenario_slug в формат БД-таблиц. Слаги живут в `scenarios.metadata.tg_scenario_slug` JSONB, чтобы не тащить ALTER TABLE для каждой воронки.
- НЕ передавать первое сообщение пользователя в welcome-текст. Welcome — приветствие, не реакция. Контекст входящего идёт в Telegram через short_id.
- НЕ закрывать `_welcome_key` или `was_welcome_sent` как private API. Они нужны в тестах.
- НЕ добавлять зависимости вне списка из Task 01.

---

## Зависимости задачи

- Tasks 01, 03, 04, 06, 07 применены
- В `bot_purify` уже применён `TASK_social_inbox_integration.md` (для реального e2e — но не требуется для acceptance в этой задаче, тесты работают через FakeProvider)

---

## Что после этой задачи

После применения Task 08 у нас работает первый реальный сценарий — welcome. Дальше:

- **Task 09** — Comment-to-DM scenario: пользователь пишет ключевое слово в комментарии под Reels → получает welcome-подобный DM
- **Task 11** — `/api/lead/{short_id}` endpoint, который дёргает bot_purify при переходе по deep-link

После Task 11 будет полноценный end-to-end через **реальный** bot_purify (без FakeProvider) — пользователь нажимает «Перейти в Telegram», попадает в bot_purify, bot_purify дёргает наш endpoint, получает контекст, продолжает квиз. Это первый момент, когда воронку можно показывать Юле.

---

**Дата создания:** 2026-04-30
**Применять в:** `D:\Work\social_inbox` после Tasks 01, 03, 04, 06, 07
**Эстимейт:** 3–4 часа на Claude Code + ручная проверка
