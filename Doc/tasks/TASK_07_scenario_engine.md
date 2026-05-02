# Task 07: ScenarioEngine + KeywordMatcher

> Применить в `D:\Work\social_inbox` после Tasks 01, 03, 04, 06. Положить файл в корень проекта, открыть в VS Code, попросить Claude Code: «Прочитай TASK_07_scenario_engine.md и выполни все подзадачи последовательно. Перед каждой подзадачей кратко опиши план, после — что фактически сделано».

---

## Контекст

После Task 06 worker умеет:
- принять событие из очереди
- найти/создать `social_user`, `conversation`
- записать входящее сообщение в `messages`

Чего не хватает: **реакции на событие**. Worker никому не отвечает. Это Task 07.

В этой задаче появляется:

1. **`KeywordMatcher`** — сервис, который принимает текст + список ключевых слов из БД и возвращает первое совпадение по приоритету. Поддерживает `exact` / `contains` / `regex`. Кэширует ключевые слова в памяти на 60 секунд.
2. **`ScenarioEngine`** — оркестратор. Принимает входящее событие → решает, какой сценарий запустить → возвращает `OutgoingMessage | None`. Сценарии регистрируются через декоратор `@register_scenario`.
3. **`RateLimiter`** — базовая защита: не больше 5 ответов в минуту на пользователя. Через Redis INCR.
4. **`EchoScenario`** — простейший рабочий сценарий: отвечает «получено: <текст>». Не финальный продукт, но демонстрирует весь pipeline. Реальные сценарии (welcome, comment-to-dm, FAQ) — в Tasks 08, 09, 13.
5. **Расширение `process_incoming_event`** — после записи входящего сообщения вызвать ScenarioEngine, отправить ответ через провайдера, записать исходящее в `messages`.

После применения этой задачи:
- POST /webhooks/sendpulse → событие → worker → запись в БД → запуск EchoScenario → отправка ответа через FakeProvider → запись исходящего в БД.
- Полный круг видим в логах и в БД.
- KeywordMatcher работает и покрыт тестами.
- Готов фундамент для Tasks 08–13, где будут писаться реальные сценарии.

---

## Цель

После выполнения этой задачи:

- Существуют `app/services/keyword_matcher.py`, `app/services/scenario_engine.py`, `app/services/rate_limiter.py`
- Существуют `app/repos/keywords.py`, `app/repos/scenarios.py` (минимальные методы для Engine)
- В `app/services/scenarios/` лежит `echo.py` с одним сценарием `EchoScenario`, зарегистрированным через декоратор
- `process_incoming_event` расширен: после записи входящего вызывает Engine и отправляет ответ
- Тесты покрывают: KeywordMatcher (все типы matchа + кэш + приоритет), ScenarioEngine (registry + routing), RateLimiter (счёт окна), e2e через FakeProvider (входящее → исходящее)
- `make lint` и `make test` зелёные
- В БД через миграцию посеян одна запись scenarios (`echo_scenario`) для интеграционных тестов

---

## Подзадачи

### 1. Репозиторий keywords

a) Создать `app/repos/keywords.py`:

```python
"""Repository for keywords table — used by KeywordMatcher.

Keywords are loaded as a list and cached in memory by KeywordMatcher.
This repo only provides bulk read; mutations happen via admin API (Task 15).
"""
from __future__ import annotations

from typing import Literal

import asyncpg

from app.repos.pool import get_pool

KeywordContext = Literal["dm", "comment", "both"]


async def list_active(context: KeywordContext) -> list[asyncpg.Record]:
    """Return active keywords applicable to the given context, ordered by priority asc."""
    pool = await get_pool()
    return await pool.fetch(
        """
        SELECT id, keyword, match_type, context, scenario_id, priority, case_sensitive
        FROM keywords
        WHERE active = TRUE
          AND context IN ($1, 'both')
        ORDER BY priority ASC, id ASC
        """,
        context,
    )
```

### 2. Репозиторий scenarios

a) Создать `app/repos/scenarios.py`:

```python
"""Repository for scenarios table.

Used by ScenarioEngine to look up scenario records by id or by name/type.
"""
from __future__ import annotations

import asyncpg

from app.repos.pool import get_pool


async def get_by_id(scenario_id: int) -> asyncpg.Record | None:
    pool = await get_pool()
    return await pool.fetchrow(
        "SELECT * FROM scenarios WHERE id = $1 AND active = TRUE",
        scenario_id,
    )


async def get_by_name(name: str) -> asyncpg.Record | None:
    pool = await get_pool()
    return await pool.fetchrow(
        "SELECT * FROM scenarios WHERE name = $1 AND active = TRUE",
        name,
    )


async def get_default_welcome() -> asyncpg.Record | None:
    """Return the first active scenario of type='welcome'.

    Used when an unknown user sends their first DM and there's no keyword match.
    Returns None if no welcome scenario is configured.
    """
    pool = await get_pool()
    return await pool.fetchrow(
        """
        SELECT * FROM scenarios
        WHERE type = 'welcome' AND active = TRUE
        ORDER BY id ASC
        LIMIT 1
        """,
    )
```

### 3. KeywordMatcher

a) Создать `app/services/keyword_matcher.py`:

```python
"""Match a text against keywords loaded from DB.

Strategy:
- Cache keywords in memory for 60 seconds (avoid DB hit on every message)
- Sort by (priority asc, id asc) — first match wins
- Three match types: exact, contains, regex
- case_sensitive flag is per-keyword

Used by ScenarioEngine to decide which scenario (if any) to trigger.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Literal

from app.repos import keywords as keywords_repo
from app.utils.logging import get_logger

log = get_logger(__name__)

CACHE_TTL_SECONDS = 60.0

KeywordContext = Literal["dm", "comment", "both"]
MatchType = Literal["exact", "contains", "regex"]


@dataclass(frozen=True)
class KeywordMatch:
    """Result of a successful match."""
    keyword_id: int
    keyword: str
    scenario_id: int
    matched_text: str


@dataclass(frozen=True)
class _Compiled:
    """A keyword pre-compiled for fast matching."""
    keyword_id: int
    keyword: str
    match_type: MatchType
    scenario_id: int
    priority: int
    case_sensitive: bool
    regex: re.Pattern[str] | None  # only set when match_type='regex'


# Module-level cache. Reset via reset_cache() in tests.
_cache: dict[KeywordContext, list[_Compiled]] = {}
_cache_loaded_at: dict[KeywordContext, float] = {}


async def _load(context: KeywordContext) -> list[_Compiled]:
    """Load keywords from DB and compile patterns."""
    rows = await keywords_repo.list_active(context)
    compiled: list[_Compiled] = []
    for row in rows:
        regex: re.Pattern[str] | None = None
        if row["match_type"] == "regex":
            try:
                flags = 0 if row["case_sensitive"] else re.IGNORECASE
                regex = re.compile(row["keyword"], flags)
            except re.error as exc:
                log.warning(
                    "keyword_regex_invalid",
                    keyword_id=row["id"],
                    keyword=row["keyword"],
                    error=str(exc),
                )
                continue
        compiled.append(_Compiled(
            keyword_id=row["id"],
            keyword=row["keyword"],
            match_type=row["match_type"],
            scenario_id=row["scenario_id"],
            priority=row["priority"],
            case_sensitive=row["case_sensitive"],
            regex=regex,
        ))
    return compiled


async def _get_or_load(context: KeywordContext) -> list[_Compiled]:
    """Return cached keywords or re-load if cache expired."""
    now = time.monotonic()
    loaded_at = _cache_loaded_at.get(context, 0.0)
    if context in _cache and (now - loaded_at) < CACHE_TTL_SECONDS:
        return _cache[context]
    compiled = await _load(context)
    _cache[context] = compiled
    _cache_loaded_at[context] = now
    log.debug("keyword_cache_refreshed", context=context, count=len(compiled))
    return compiled


def _matches(c: _Compiled, text: str) -> bool:
    """Check if a single compiled keyword matches the text."""
    if c.match_type == "regex":
        assert c.regex is not None
        return bool(c.regex.search(text))

    target = text if c.case_sensitive else text.lower()
    needle = c.keyword if c.case_sensitive else c.keyword.lower()

    if c.match_type == "exact":
        return target.strip() == needle
    if c.match_type == "contains":
        return needle in target
    return False


async def match(text: str, context: KeywordContext) -> KeywordMatch | None:
    """Return first matching keyword (by priority) or None.

    Empty/None text returns None — keyword matching only applies to text events.
    """
    if not text:
        return None
    compiled = await _get_or_load(context)
    for c in compiled:
        if _matches(c, text):
            return KeywordMatch(
                keyword_id=c.keyword_id,
                keyword=c.keyword,
                scenario_id=c.scenario_id,
                matched_text=text,
            )
    return None


def reset_cache() -> None:
    """Reset in-memory cache. Tests-only."""
    _cache.clear()
    _cache_loaded_at.clear()
```

### 4. RateLimiter

a) Создать `app/services/rate_limiter.py`:

```python
"""Token-bucket-style rate limiter on Redis.

For Task 07 we use a simple sliding-window via INCR + EXPIRE:
- Each (user, action) pair has a counter in Redis
- Counter expires after `window_seconds`
- If counter exceeds `limit`, deny

Limits configured here (intentionally simple for MVP):
- replies_per_user_per_minute: 5

Future limits (not in this task — see CLAUDE.md §12.4):
- 10 replies per user per day
- 1 welcome per user lifetime
- 1 comment-to-DM per (user, scenario) per 30 days
These will be implemented in Tasks 08/09 with their own keys.
"""
from __future__ import annotations

from app.repos.redis_client import get_redis
from app.utils.logging import get_logger

log = get_logger(__name__)

# Defaults
REPLIES_PER_MINUTE_LIMIT = 5
REPLIES_PER_MINUTE_WINDOW = 60


async def check_and_increment(
    key: str,
    limit: int,
    window_seconds: int,
) -> bool:
    """Atomically increment counter; return True if within limit, False if over.

    Uses INCR (creates key if missing) and sets EXPIRE only on first hit.
    This is the standard sliding-window approximation and good enough for our scale.
    """
    redis = await get_redis()
    count = await redis.incr(key)
    if count == 1:
        await redis.expire(key, window_seconds)
    return count <= limit


async def can_reply(user_id: int) -> bool:
    """Returns True if we can reply to this user right now.

    Checks the per-minute reply limit. If over — caller should NOT send the message
    and should log the throttle.
    """
    key = f"rl:reply:{user_id}"
    allowed = await check_and_increment(
        key, REPLIES_PER_MINUTE_LIMIT, REPLIES_PER_MINUTE_WINDOW,
    )
    if not allowed:
        log.warning("rate_limit_hit_replies_per_minute", user_id=user_id)
    return allowed
```

### 5. ScenarioEngine

a) Создать `app/services/scenario_engine.py`:

```python
"""ScenarioEngine — routes incoming events to the correct scenario handler.

Architecture:
- Scenario handlers register themselves via @register_scenario('type_name')
- Engine.handle() looks at the event + DB context and chooses a handler
- Handler returns OutgoingMessage | None
- None means "no reply" (e.g. event was processed silently)

Routing logic (in Task 07):
1. If event_type='comment' AND keyword matched → run scenario from keyword.scenario_id
2. If event_type='message' AND keyword matched → run scenario from keyword.scenario_id
3. If event_type='message' AND user is brand new (first DM) → run default welcome
4. Otherwise → run echo (catch-all for testing in this task; replaced by FAQ/Smart in Task 13)

Future routing rules (Tasks 08+):
- Re-engagement (last seen >30 days ago) → re-engagement welcome
- "оператор" / "human" keyword → handover scenario
- Conversation in handover_pending → no auto-reply
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import asyncpg

from app.models.events import IncomingEvent, OutgoingMessage
from app.repos import keywords as keywords_repo  # noqa: F401  # import side-effect: schema usage
from app.repos import scenarios as scenarios_repo
from app.services.keyword_matcher import KeywordMatch, match as match_keywords
from app.utils.logging import get_logger

log = get_logger(__name__)

# Handler signature: (event, user, conversation, scenario_row) -> OutgoingMessage | None
ScenarioHandler = Callable[
    [IncomingEvent, asyncpg.Record, asyncpg.Record, asyncpg.Record],
    Awaitable[OutgoingMessage | None],
]


_registry: dict[str, ScenarioHandler] = {}


def register_scenario(scenario_type: str) -> Callable[[ScenarioHandler], ScenarioHandler]:
    """Decorator: register a handler for a scenarios.type value.

    Example:
        @register_scenario('echo')
        async def handle_echo(event, user, conv, scenario):
            return OutgoingMessage(...)
    """
    def decorator(fn: ScenarioHandler) -> ScenarioHandler:
        if scenario_type in _registry:
            raise RuntimeError(
                f"Scenario type {scenario_type!r} already registered: "
                f"existing={_registry[scenario_type].__name__}, new={fn.__name__}"
            )
        _registry[scenario_type] = fn
        log.info("scenario_registered", type=scenario_type, handler=fn.__name__)
        return fn
    return decorator


def get_handler(scenario_type: str) -> ScenarioHandler | None:
    """Return registered handler for a type, or None."""
    return _registry.get(scenario_type)


def reset_registry() -> None:
    """Clear handler registry. Tests-only."""
    _registry.clear()


async def handle(
    event: IncomingEvent,
    user: asyncpg.Record,
    conversation: asyncpg.Record,
    is_new_user: bool,
) -> OutgoingMessage | None:
    """Route an event to the right scenario and return its OutgoingMessage (or None).

    Args:
        event: incoming event (message or comment)
        user: social_users row (just created or fetched)
        conversation: conversations row (active)
        is_new_user: True if `user` was created as part of processing this event
    """
    # Skip if conversation is in handover state — humans take over.
    if conversation["status"] in ("handover_pending", "handover_done"):
        log.info(
            "scenario_skipped_handover",
            user_id=user["id"],
            conv_status=conversation["status"],
        )
        return None

    # Determine keyword context
    context = "comment" if event.event_type == "comment" else "dm"

    # 1. Try keyword match
    km: KeywordMatch | None = None
    if event.text:
        km = await match_keywords(event.text, context)

    scenario_row: asyncpg.Record | None = None

    if km:
        scenario_row = await scenarios_repo.get_by_id(km.scenario_id)
        if scenario_row is None:
            log.warning(
                "keyword_matched_but_scenario_missing",
                keyword_id=km.keyword_id,
                scenario_id=km.scenario_id,
            )

    # 2. New user, no keyword match → default welcome
    if scenario_row is None and is_new_user and event.event_type == "message":
        scenario_row = await scenarios_repo.get_default_welcome()

    # 3. Fallback: echo scenario (testing only — replaced in Task 13 by smart/FAQ)
    if scenario_row is None:
        scenario_row = await scenarios_repo.get_by_name("echo_scenario")
        if scenario_row is None:
            log.warning("no_scenario_resolved_and_echo_missing")
            return None

    # Dispatch
    handler = get_handler(scenario_row["type"])
    if handler is None:
        log.warning(
            "no_handler_for_scenario_type",
            scenario_id=scenario_row["id"],
            scenario_type=scenario_row["type"],
        )
        return None

    log.info(
        "scenario_dispatch",
        scenario_id=scenario_row["id"],
        scenario_type=scenario_row["type"],
        scenario_name=scenario_row["name"],
        user_id=user["id"],
    )

    return await handler(event, user, conversation, scenario_row)
```

### 6. EchoScenario

a) Создать `app/services/scenarios/__init__.py`:

```python
"""Scenario implementations.

Each scenario lives in its own module. Importing this package
triggers handler registration via the @register_scenario decorator.

ScenarioEngine relies on this side-effect: when the package is imported
(e.g. at app startup), all handlers become available in the registry.
"""
from app.services.scenarios import echo  # noqa: F401 — side effect only

__all__ = ["echo"]
```

b) Создать `app/services/scenarios/echo.py`:

```python
"""Echo scenario — minimal working scenario for end-to-end testing.

Replies with "Получено: <text>". Used in tests and as a catch-all fallback
in early development. To be replaced by FAQ/Smart in Task 13.

Registered as type='echo' (also as type='smart' fallback in tests).
"""
from __future__ import annotations

import asyncpg

from app.models.events import IncomingEvent, OutgoingMessage
from app.services.scenario_engine import register_scenario


@register_scenario("echo")
async def handle_echo(
    event: IncomingEvent,
    user: asyncpg.Record,
    conversation: asyncpg.Record,
    scenario: asyncpg.Record,
) -> OutgoingMessage | None:
    text_in = event.text or ""
    reply = f"Получено: {text_in[:200]}"
    return OutgoingMessage(
        platform=event.platform,
        external_user_id=event.external_user_id,
        text=reply,
        scenario_id=scenario["id"],
    )
```

### 7. Сид echo_scenario

a) Создать миграцию `migrations/005_seed_echo_scenario.sql`:

```sql
-- Migration 005: Seed echo_scenario for end-to-end testing.
-- This row is the fallback used by ScenarioEngine when no keyword matches
-- and the user is not new. Replaced by smart/FAQ scenario in Task 13.

INSERT INTO scenarios (name, type, template, active)
VALUES ('echo_scenario', 'echo', 'Received: {text}', TRUE)
ON CONFLICT (name) DO NOTHING;
```

### 8. Расширение worker'а

a) Обновить `app/workers/tasks_messages.py` — после записи входящего вызывать ScenarioEngine, отправлять ответ через провайдер, записывать исходящее.

   Полный новый файл:

```python
"""arq task: process a single IncomingEvent.

Pipeline (post-Task 07):
1. Idempotency: if events_log.processed_at already set → skip
2. Find or create social_user (track is_new_user)
3. Find or create active conversation
4. Insert incoming message (direction='in')
5. Bump last_message_at on user and conversation
6. Run ScenarioEngine.handle() → maybe OutgoingMessage
7. If reply produced AND rate limit allows AND not blocked by safety:
    a. provider.send(OutgoingMessage) → external_message_id
    b. Insert outgoing message (direction='out')
8. Mark events_log row as processed

Future expansions (later tasks):
- Task 13: Claude-based smart scenarios + tool use for handover
- Task 14: safety filters on outgoing messages
- Task 14: per-user daily/lifetime rate limits
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from app.models.events import IncomingEvent, OutgoingMessage
from app.providers import get_provider
from app.repos import conversations, events as events_repo, messages, users
from app.services import scenario_engine
from app.services.rate_limiter import can_reply
from app.utils.logging import get_logger

# Importing this package triggers @register_scenario side-effects.
import app.services.scenarios  # noqa: F401

log = get_logger(__name__)


async def process_incoming_event(
    ctx: dict[str, Any],
    event_dict: dict[str, Any],
    log_id: int,
) -> None:
    event = IncomingEvent.model_validate(event_dict)

    # 1. Idempotency
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
        # 2. User
        user = await users.get_by_external(
            event.provider, event.platform, event.external_user_id,
        )
        is_new_user = user is None
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

        # 3. Conversation
        conv = await conversations.get_or_create(user["id"], event.platform)

        # 4. Insert incoming
        await messages.insert(
            conversation_id=conv["id"],
            direction="in",
            text=event.text,
            media_url=event.media_url,
            source=_source_from_event_type(event.event_type),
            external_message_id=event.external_event_id,
            raw_payload=event.raw_payload,
        )

        # 5. Bump timestamps
        await users.update_last_message_at(user["id"], event.occurred_at)
        await conversations.update_last_message_at(conv["id"], event.occurred_at)

        # 6. Scenario engine
        outgoing = await scenario_engine.handle(event, user, conv, is_new_user=is_new_user)

        # 7. Send reply if produced
        if outgoing is not None:
            await _send_and_record(outgoing, conv["id"], user["id"])

        # 8. Mark processed
        await events_repo.mark_processed(log_id, error=None)
        log.info("event_processed_ok", log_id=log_id, user_id=user["id"], conv_id=conv["id"])

    except Exception as exc:
        log.exception("event_processing_failed", log_id=log_id, error=str(exc))
        await events_repo.mark_processed(log_id, error=str(exc)[:500])
        raise


async def _send_and_record(
    outgoing: OutgoingMessage,
    conversation_id: int,
    user_id: int,
) -> None:
    """Send outbound message via provider and record it in messages table."""
    if not await can_reply(user_id):
        log.warning("reply_throttled", user_id=user_id)
        return

    provider = get_provider()
    try:
        external_id = await provider.send(outgoing)
    except Exception as exc:
        log.exception("provider_send_failed", user_id=user_id, error=str(exc))
        external_id = None

    # If provider didn't return an ID (failure or unsupported), generate one
    # to satisfy the UNIQUE constraint on messages.external_message_id.
    # This is a local-only ID with prefix 'local:' to make it distinguishable.
    record_external_id = external_id if external_id else f"local:{uuid.uuid4()}"

    await messages.insert(
        conversation_id=conversation_id,
        direction="out",
        text=outgoing.text,
        media_url=outgoing.media_url,
        source="reply",
        scenario_id=outgoing.scenario_id,
        external_message_id=record_external_id,
    )

    log.info(
        "outgoing_sent",
        user_id=user_id,
        scenario_id=outgoing.scenario_id,
        send_ok=external_id is not None,
    )


def _source_from_event_type(event_type: str) -> str:
    return {
        "message": "dm",
        "comment": "comment",
        "postback": "postback",
    }.get(event_type, "unknown")
```

### 9. Тесты

a) Создать `tests/test_keyword_matcher.py`:

```python
"""Tests for KeywordMatcher."""
from __future__ import annotations

import pytest

from app.services.keyword_matcher import match, reset_cache


@pytest.fixture(autouse=True)
def _reset_kw_cache() -> None:
    reset_cache()
    yield
    reset_cache()


async def _seed_scenario(db, name: str = "test_scenario", type_: str = "echo") -> int:
    row = await db.fetchrow(
        """
        INSERT INTO scenarios (name, type, active) VALUES ($1, $2, TRUE)
        RETURNING id
        """,
        name, type_,
    )
    return row["id"]


async def _seed_keyword(
    db, *,
    keyword: str,
    match_type: str,
    context: str,
    scenario_id: int,
    priority: int = 100,
    case_sensitive: bool = False,
) -> int:
    row = await db.fetchrow(
        """
        INSERT INTO keywords (keyword, match_type, context, scenario_id, priority, case_sensitive)
        VALUES ($1, $2, $3, $4, $5, $6)
        RETURNING id
        """,
        keyword, match_type, context, scenario_id, priority, case_sensitive,
    )
    return row["id"]


@pytest.mark.asyncio
async def test_exact_match(db) -> None:
    sid = await _seed_scenario(db, "s_exact")
    await _seed_keyword(db, keyword="ОЧИЩЕНИЕ", match_type="exact", context="dm", scenario_id=sid)

    m = await match("ОЧИЩЕНИЕ", "dm")
    assert m is not None
    assert m.scenario_id == sid

    m2 = await match("ОЧИЩЕНИЕ программа", "dm")
    assert m2 is None  # exact doesn't match substring


@pytest.mark.asyncio
async def test_contains_match(db) -> None:
    sid = await _seed_scenario(db, "s_contains")
    await _seed_keyword(db, keyword="масла", match_type="contains", context="dm", scenario_id=sid)

    m = await match("Расскажи про масла doTERRA", "dm")
    assert m is not None
    assert m.scenario_id == sid


@pytest.mark.asyncio
async def test_regex_match(db) -> None:
    sid = await _seed_scenario(db, "s_regex")
    await _seed_keyword(
        db, keyword=r"\bпробник\w*\b", match_type="regex",
        context="dm", scenario_id=sid,
    )

    m = await match("Хочу пробники", "dm")
    assert m is not None
    assert m.scenario_id == sid


@pytest.mark.asyncio
async def test_priority_ordering(db) -> None:
    s_low = await _seed_scenario(db, "s_priority_low")
    s_high = await _seed_scenario(db, "s_priority_high")
    # priority=10 (higher prio: lower number)
    await _seed_keyword(
        db, keyword="hello", match_type="contains",
        context="dm", scenario_id=s_high, priority=10,
    )
    # priority=100 (lower prio)
    await _seed_keyword(
        db, keyword="hello", match_type="contains",
        context="dm", scenario_id=s_low, priority=100,
    )

    m = await match("hello world", "dm")
    assert m is not None
    assert m.scenario_id == s_high  # higher priority wins


@pytest.mark.asyncio
async def test_case_insensitive_default(db) -> None:
    sid = await _seed_scenario(db, "s_case")
    await _seed_keyword(
        db, keyword="ОЧИЩЕНИЕ", match_type="exact",
        context="dm", scenario_id=sid, case_sensitive=False,
    )

    m = await match("очищение", "dm")
    assert m is not None


@pytest.mark.asyncio
async def test_case_sensitive_strict(db) -> None:
    sid = await _seed_scenario(db, "s_case_strict")
    await _seed_keyword(
        db, keyword="DETOX", match_type="exact",
        context="dm", scenario_id=sid, case_sensitive=True,
    )

    assert await match("detox", "dm") is None
    assert await match("DETOX", "dm") is not None


@pytest.mark.asyncio
async def test_context_filter(db) -> None:
    sid = await _seed_scenario(db, "s_ctx")
    await _seed_keyword(
        db, keyword="hi", match_type="exact",
        context="comment", scenario_id=sid,
    )

    assert await match("hi", "dm") is None      # different context
    assert await match("hi", "comment") is not None


@pytest.mark.asyncio
async def test_both_context_matches_either(db) -> None:
    sid = await _seed_scenario(db, "s_both")
    await _seed_keyword(
        db, keyword="hi", match_type="exact",
        context="both", scenario_id=sid,
    )

    assert await match("hi", "dm") is not None
    assert await match("hi", "comment") is not None


@pytest.mark.asyncio
async def test_invalid_regex_skipped(db) -> None:
    sid = await _seed_scenario(db, "s_bad_re")
    await _seed_keyword(
        db, keyword="[unclosed", match_type="regex",
        context="dm", scenario_id=sid,
    )

    # Doesn't crash, just skips the invalid pattern.
    assert await match("anything", "dm") is None


@pytest.mark.asyncio
async def test_empty_text_returns_none(db) -> None:
    sid = await _seed_scenario(db, "s_empty")
    await _seed_keyword(
        db, keyword="hi", match_type="exact",
        context="dm", scenario_id=sid,
    )

    assert await match("", "dm") is None
    assert await match(None, "dm") is None  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_cache_reused_within_ttl(db, monkeypatch: pytest.MonkeyPatch) -> None:
    """Second call within TTL doesn't hit DB."""
    sid = await _seed_scenario(db, "s_cache")
    await _seed_keyword(
        db, keyword="cached", match_type="contains",
        context="dm", scenario_id=sid,
    )

    # Prime cache
    m1 = await match("text with cached word", "dm")
    assert m1 is not None

    # Now if we add a NEW keyword without resetting cache, it shouldn't be visible
    sid2 = await _seed_scenario(db, "s_cache_new")
    await _seed_keyword(
        db, keyword="newone", match_type="contains",
        context="dm", scenario_id=sid2,
    )
    m2 = await match("text with newone in it", "dm")
    assert m2 is None  # cached snapshot doesn't include the new keyword
```

b) Создать `tests/test_rate_limiter.py`:

```python
"""Tests for RateLimiter."""
from __future__ import annotations

import pytest

from app.repos.redis_client import get_redis
from app.services.rate_limiter import (
    REPLIES_PER_MINUTE_LIMIT,
    can_reply,
    check_and_increment,
)


@pytest.mark.asyncio
async def test_check_and_increment_allows_under_limit() -> None:
    redis = await get_redis()
    await redis.delete("rl:test:1")
    for _ in range(3):
        ok = await check_and_increment("rl:test:1", limit=5, window_seconds=60)
        assert ok is True


@pytest.mark.asyncio
async def test_check_and_increment_blocks_over_limit() -> None:
    redis = await get_redis()
    await redis.delete("rl:test:2")
    for _ in range(5):
        await check_and_increment("rl:test:2", limit=5, window_seconds=60)
    # Sixth call: blocked
    ok = await check_and_increment("rl:test:2", limit=5, window_seconds=60)
    assert ok is False


@pytest.mark.asyncio
async def test_can_reply_uses_per_user_key() -> None:
    redis = await get_redis()
    user_id = 9999
    await redis.delete(f"rl:reply:{user_id}")

    for _ in range(REPLIES_PER_MINUTE_LIMIT):
        ok = await can_reply(user_id)
        assert ok is True
    # Next one is over the limit
    assert await can_reply(user_id) is False
```

c) Создать `tests/test_scenario_engine.py`:

```python
"""Tests for ScenarioEngine routing."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.models.events import IncomingEvent
from app.repos import conversations, scenarios as scenarios_repo, users
from app.services import scenario_engine
from app.services.keyword_matcher import reset_cache


@pytest.fixture(autouse=True)
def _reset_state() -> None:
    reset_cache()
    yield
    reset_cache()
    # Note: do NOT reset_registry() — handlers are registered at import time
    # and tests share the same process. Resetting would break subsequent tests.


def _make_event(
    text: str = "hi",
    event_type: str = "message",
    external_user_id: str = "engine_user",
    external_event_id: str = "evt_eng_1",
) -> IncomingEvent:
    return IncomingEvent(
        provider="sendpulse",
        platform="instagram",
        event_type=event_type,
        external_user_id=external_user_id,
        external_event_id=external_event_id,
        text=text,
        occurred_at=datetime(2026, 4, 30, 12, 0, tzinfo=UTC),
    )


@pytest.mark.asyncio
async def test_engine_falls_back_to_echo_when_no_match(db) -> None:
    """No keywords seeded, no welcome → echo fallback fires."""
    user = await users.create(
        provider_name="sendpulse", platform="instagram", external_id="engine_no_match",
    )
    conv = await conversations.create(user["id"], "instagram")

    event = _make_event(text="random text", external_user_id="engine_no_match")
    msg = await scenario_engine.handle(event, user, conv, is_new_user=False)

    assert msg is not None
    assert msg.text is not None
    assert "Получено:" in msg.text
    assert "random text" in msg.text


@pytest.mark.asyncio
async def test_engine_returns_none_for_handover_conversation(db) -> None:
    user = await users.create(
        provider_name="sendpulse", platform="instagram", external_id="engine_handover",
    )
    conv = await conversations.create(user["id"], "instagram")
    await conversations.set_status(conv["id"], "handover_pending", reason="test")
    conv_after = await conversations.get_active(user["id"], "instagram")

    # After set_status, get_active won't find the conversation (filter status='active').
    # Use direct fetch instead.
    conv_handover = await db.fetchrow("SELECT * FROM conversations WHERE id = $1", conv["id"])

    event = _make_event(external_user_id="engine_handover")
    msg = await scenario_engine.handle(event, user, conv_handover, is_new_user=False)

    assert msg is None  # handover state suppresses replies


@pytest.mark.asyncio
async def test_engine_uses_keyword_scenario(db) -> None:
    """When a keyword matches, the keyword's scenario_id is used."""
    # Create custom scenario with type='echo' (so existing handler picks it up)
    sid_row = await db.fetchrow(
        """
        INSERT INTO scenarios (name, type, template, active)
        VALUES ('keyword_test_scenario', 'echo', 'kw template', TRUE)
        RETURNING id
        """,
    )
    sid = sid_row["id"]

    await db.execute(
        """
        INSERT INTO keywords (keyword, match_type, context, scenario_id, priority)
        VALUES ($1, 'exact', 'dm', $2, 10)
        """,
        "ОЧИЩЕНИЕ", sid,
    )

    user = await users.create(
        provider_name="sendpulse", platform="instagram", external_id="engine_kw",
    )
    conv = await conversations.create(user["id"], "instagram")

    event = _make_event(text="ОЧИЩЕНИЕ", external_user_id="engine_kw")
    msg = await scenario_engine.handle(event, user, conv, is_new_user=False)

    assert msg is not None
    assert msg.scenario_id == sid


@pytest.mark.asyncio
async def test_register_scenario_duplicate_raises() -> None:
    """Registering same type twice should raise."""
    from app.services.scenario_engine import register_scenario

    @register_scenario("__test_dup")
    async def first(event, user, conv, scenario):  # noqa: D401, ANN001, ANN201
        return None

    with pytest.raises(RuntimeError, match="already registered"):
        @register_scenario("__test_dup")
        async def second(event, user, conv, scenario):  # noqa: D401, ANN001, ANN201
            return None
```

d) Создать `tests/test_e2e_pipeline.py`:

```python
"""End-to-end test: webhook → DB → scenario → reply → DB.

The biggest payoff test of Task 07: confirms the full pipeline works.
"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest
from httpx import AsyncClient

from app.models.events import IncomingEvent
from app.repos import users
from app.workers.tasks_messages import process_incoming_event
from app.repos import events as events_repo
from tests.fakes.fake_provider import FakeProvider


@pytest.mark.asyncio
async def test_full_pipeline_echo(
    client: AsyncClient,
    fake_provider: FakeProvider,
    db,
) -> None:
    """Webhook → events_log → worker → scenario → provider.send → outgoing message in DB."""
    event = IncomingEvent(
        provider="sendpulse",
        platform="instagram",
        event_type="message",
        external_user_id="e2e_user_1",
        external_event_id="e2e_evt_1",
        username="e2e_user",
        text="привет",
        occurred_at=datetime(2026, 4, 30, 12, 0, tzinfo=UTC),
    )
    fake_provider.queue_event(event)

    # 1. POST webhook → records to events_log AND enqueues via arq.
    # Since we don't run arq runtime in tests, manually drive the worker step.
    response = await client.post("/webhooks/sendpulse", json={})
    assert response.status_code == 200

    log_row = await db.fetchrow(
        "SELECT * FROM events_log WHERE external_event_id = $1",
        "e2e_evt_1",
    )
    assert log_row is not None
    assert log_row["processed_at"] is None  # worker hasn't run yet

    # 2. Drive the worker manually
    await process_incoming_event({}, event.model_dump(mode="json"), log_row["id"])

    # 3. Verify processed
    log_after = await db.fetchrow(
        "SELECT * FROM events_log WHERE id = $1", log_row["id"],
    )
    assert log_after["processed_at"] is not None
    assert log_after["error"] is None

    # 4. User exists
    user = await users.get_by_external("sendpulse", "instagram", "e2e_user_1")
    assert user is not None

    # 5. Two messages: incoming and outgoing
    msgs = await db.fetch(
        """
        SELECT m.* FROM messages m
        JOIN conversations c ON c.id = m.conversation_id
        WHERE c.user_id = $1
        ORDER BY m.created_at ASC
        """,
        user["id"],
    )
    assert len(msgs) == 2
    assert msgs[0]["direction"] == "in"
    assert msgs[0]["text"] == "привет"
    assert msgs[1]["direction"] == "out"
    assert "Получено:" in msgs[1]["text"]
    assert msgs[1]["scenario_id"] is not None

    # 6. Provider received the outgoing message
    assert len(fake_provider.sent) == 1
    sent = fake_provider.sent[0]
    assert sent.platform == "instagram"
    assert sent.external_user_id == "e2e_user_1"
    assert "Получено:" in (sent.text or "")
```

---

## Acceptance criteria

После выполнения всех подзадач выполнить и поставить галочки:

- [ ] Файлы созданы по структуре подзадач 1–8
- [ ] Миграция 005 создаётся при старте api: `docker compose exec postgres psql -U social_inbox -d social_inbox -c "SELECT name, type FROM scenarios WHERE name='echo_scenario'"` возвращает 1 строку
- [ ] `make lint` проходит без ошибок
- [ ] `make test` проходит, все новые тесты зелёные:
  - `test_keyword_matcher.py` — 11 тестов
  - `test_rate_limiter.py` — 3 теста
  - `test_scenario_engine.py` — 4 теста
  - `test_e2e_pipeline.py` — 1 ключевой тест
  - Все существующие тесты Tasks 01, 03, 04, 06 продолжают работать
- [ ] Логи worker'а при обработке события показывают цепочку:
  ```
  event_processing
  user_created (or not)
  scenario_registered echo handle_echo  (на старте)
  scenario_dispatch type=echo
  outgoing_sent
  event_processed_ok
  ```
- [ ] При e2e-проверке через FakeProvider в тесте видна полная цепочка: webhook → events_log → user → conversation → in-message → scenario → out-message → provider.sent
- [ ] Идемпотентность: при повторном POST с тем же external_event_id и повторном вызове `process_incoming_event` не создаётся второго ответа
- [ ] Rate-limit: 6-й подряд `can_reply(user_id)` за минуту возвращает False (это ловит test_rate_limiter)

---

## Do NOT

- НЕ реализовывать в этой задаче welcome/comment-to-DM/FAQ/smart-сценарии. Только EchoScenario для проверки pipeline. Реальные сценарии — Tasks 08, 09, 13.
- НЕ вызывать Claude API. EchoScenario — это шаблонный текст, никакого AI.
- НЕ применять safety-фильтры. Это Task 14. Echo — просто prefixed-эхо без проверки.
- НЕ хардкодить keywords в коде. Они в БД и грузятся через KeywordMatcher.
- НЕ реализовывать дневные/жизненные лимиты (10/день, 1 welcome всего). Только per-minute. Полные лимиты — Task 14.
- НЕ редактировать ABC-контракт MessagingProvider. Если возникает мысль «нужен новый метод» — лучше создать сервис рядом с движком, а не менять интерфейс.
- НЕ создавать handover-сценарий в этой задаче. Текущая логика: при `status='handover_pending'` engine просто возвращает None. Полная handover-логика — Task 14.
- НЕ оптимизировать KeywordMatcher (Aho-Corasick, etc.). 60-сек кэш + линейный проход хорош для 1000+ keywords. Нагрузка не оправдывает усложнение.
- НЕ переиспользовать singleton-провайдер в тестах напрямую — только через fixture `fake_provider`. Это сохраняет тестовую изоляцию.
- НЕ добавлять зависимости вне списка из Task 01.

---

## Зависимости задачи

- Task 01 применена (структура проекта, docker-compose)
- Task 03 применена (БД + repos pool/users/conversations/messages)
- Task 04 применена (MessagingProvider, FakeProvider)
- Task 06 применена (webhook endpoint, worker, events_log repo)
- Не требует SendPulse credentials — тесты идут через FakeProvider

---

## Что после этой задачи

После применения Task 07 у нас есть полный pipeline с echo-обработчиком. Дальше:

- **Task 08** — Welcome scenario (реальное приветствие при первом DM, с deep-link в Telegram)
- **Task 09** — Comment-to-DM scenario (private replies API)
- **Task 10** — `lead_tracker` сервис: генерация deep-link для перехода в Telegram-бот, обновление `tg_handover_at`
- **Task 11** — `/api/lead/{short_id}` endpoint для bot_purify

---

**Дата создания:** 2026-04-30
**Применять в:** `D:\Work\social_inbox` после Tasks 01, 03, 04, 06
**Эстимейт:** 4–5 часов на Claude Code + ручная проверка
