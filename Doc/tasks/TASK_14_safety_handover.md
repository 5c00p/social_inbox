# Task 14: Safety filters + handover scenario

> Применить в `D:\Work\social_inbox` после Tasks 01, 03, 04, 06, 07, 08, 09, 11, 13. Положить файл в корень проекта, открыть в VS Code, попросить Claude Code: «Прочитай TASK_14_safety_handover.md и выполни все подзадачи последовательно. Перед каждой подзадачей кратко опиши план, после — что фактически сделано».

---

## Контекст

Это **юридически критическая задача**: после неё проект готов к запуску с реальной аудиторией.

После Tasks 08–13 у нас работает:
- Welcome / comment-to-DM сценарии с проверенными шаблонами из БД
- Smart scenario с Claude API
- Эскалация Claude через tool use (когда модель сама решает «нужен человек»)

**Чего не хватает с точки зрения compliance:**

1. **Защита от medical claims в Claude-ответах.** System prompt говорит Claude не делать medical claims, но это не гарантия — Claude может ошибиться, или пользователь может его обмануть промпт-инъекцией. Нужен второй слой защиты: regex-проверки на исходящие.

2. **Pre-emptive handover на симптомы.** Если пользователь пишет «у меня болит голова» — нет смысла пускать это через Claude (даже с tool use): дёшево и надёжно сразу эскалировать на Юлю.

3. **Команда «оператор».** Стандартный escape-hatch: пользователь явно просит человека → мгновенная эскалация с понятным ответом «Юля скоро ответит». Сейчас этого нет — keyword-сценарий просто не настроен.

4. **Уведомление Юле о handover.** Сейчас при эскалации в логах появляется запись, но Юля её не видит. Нужен Telegram-бот для уведомлений админу.

5. **Полные rate limits.** Per-user-day лимит из CLAUDE.md § 12.4 не реализован.

После Task 14 проект функционально готов к запуску. Останется только Task 05 (SendPulseProvider) и deployment-задачи.

---

## Цель

После выполнения этой задачи:

- `app/services/safety.py` — функции `check_outgoing` и `check_incoming` с regex-фильтрами
- `app/prompts/banned_patterns.py` — централизованный список паттернов на medical claims
- `app/services/handover.py` — единая точка перевода conversation в handover_pending + уведомление Юле
- `app/services/notifications.py` — Telegram-бот для уведомлений админу
- Smart scenario применяет `check_outgoing` ПОСЛЕ Claude reply, ПЕРЕД отправкой
- Worker применяет `check_incoming` ПЕРЕД вызовом engine — на симптомы
- Новый scenario типа `'handover'` для keyword «оператор» с понятным ответом пользователю
- Расширенный rate-limiter: per-user-day (10 ответов в сутки)
- Конфиг: `notification_bot_token`, `notification_admin_chat_id`
- Тесты покрывают: regex banned patterns (положительные + ложноположительные), symptom detection, handover scenario, уведомление через мок Telegram API, per-user-day лимит
- Все исходящие, заблокированные safety, записываются в `messages` с `safety_blocked=True` (audit trail)

---

## Подзадачи

### 1. Banned patterns

a) Создать `app/prompts/banned_patterns.py`:

```python
"""Banned patterns for outgoing messages — doTERRA compliance.

Why a separate module:
- Easy to update without touching service code
- Reviewed by Yulia (and ideally by a doTERRA compliance lawyer)
- Same patterns can be re-used by future scenarios (e.g. admin pre-publish review)

Why regex, not AI:
- Deterministic, fast (<1ms)
- Can be tested exhaustively
- A semantic check (LLM-based) would be expensive and could itself hallucinate
- We use Claude system prompt for the SOFT guidance, regex as the HARD floor

How to add a new pattern:
1. Add to BANNED_PATTERNS with descriptive comment
2. Add a positive test in tests/test_safety.py (a phrase that MUST be blocked)
3. Add a negative test (a similar but allowed phrase that MUST pass)

How to test patterns interactively:
    python -c "from app.services.safety import check_outgoing; print(check_outgoing('ваш текст'))"
"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class BannedPattern:
    """A regex pattern with a human-readable label."""
    pattern: re.Pattern[str]
    label: str  # Short description used in logs and admin notifications


# Compiled at module import. All patterns are case-insensitive.
def _compile(pattern_str: str, label: str) -> BannedPattern:
    return BannedPattern(
        pattern=re.compile(pattern_str, re.IGNORECASE | re.UNICODE),
        label=label,
    )


BANNED_PATTERNS: list[BannedPattern] = [
    # --- Direct medical claims ---
    _compile(r"\bлечит\b",                              "медицинское: лечит"),
    _compile(r"\bвылечит\b",                            "медицинское: вылечит"),
    _compile(r"\bвылечив\w*\b",                         "медицинское: вылечив*"),
    _compile(r"\bизлечив\w*\b",                         "медицинское: излечив*"),
    _compile(r"\bисцел\w*\b",                           "медицинское: исцеляет"),

    # --- Disease prevention claims ---
    _compile(
        r"\bпрофилактик\w*\s+(рак|covid|гриппа|онколог\w*|диабет\w*|инфекци\w*)",
        "медицинское: профилактика конкретного заболевания",
    ),
    _compile(
        r"\bпредотвра\w+\s+(рак|covid|гриппа|онколог\w*|диабет\w*)",
        "медицинское: предотвращает заболевание",
    ),

    # --- Drug-replacement claims ---
    _compile(r"\bантибиотик",                           "медицинское: масла как антибиотики"),
    _compile(r"\bвместо\s+лекарств",                    "медицинское: вместо лекарств"),
    _compile(r"\bвместо\s+таблет\w*",                   "медицинское: вместо таблеток"),
    _compile(r"\bзамен\w+\s+(лекарств|препарат|таблет)", "медицинское: замена лекарств"),
    _compile(r"отмен\w+\s+(лекарств|препарат|таблет)",  "медицинское: отменить лекарства"),
    _compile(r"\bне\s+нужно\s+к\s+врач",                "медицинское: не нужно к врачу"),

    # --- Categorical promises ---
    _compile(r"\bгаранти(?:ру\w+|я)\b",                 "обещание: гарантирую"),
    _compile(r"\b100\s*%\s+результат",                  "обещание: 100% результат"),
    _compile(r"\bточно\s+поможет",                      "обещание: точно поможет"),
    _compile(r"\bобязательно\s+(вылеч|излеч|поможет)",  "обещание: обязательно вылечит/поможет"),

    # --- Diagnosis ---
    _compile(r"\b(у\s+вас|у\s+тебя)\s+(диагноз|симптом)", "диагностика пациента"),

    # --- Self-medication safety ---
    _compile(r"внутрь\s+без\s+консультаци",             "опасное самолечение"),
    _compile(r"\bпринимайте\s+внутрь\b",                "опасное самолечение: принимайте внутрь"),
]


@dataclass(frozen=True)
class SymptomMatch:
    """Marker that the user is reporting symptoms — pre-emptive handover."""
    keyword: str


SYMPTOM_KEYWORDS: list[re.Pattern[str]] = [
    re.compile(r"\bболит\b",                re.IGNORECASE | re.UNICODE),
    re.compile(r"\bболь\s+(в|у)\b",         re.IGNORECASE | re.UNICODE),
    re.compile(r"\bдиагноз\b",              re.IGNORECASE | re.UNICODE),
    re.compile(r"\bврач\b",                 re.IGNORECASE | re.UNICODE),
    re.compile(r"\bбольниц\w*\b",           re.IGNORECASE | re.UNICODE),
    re.compile(r"\bтаблетк\w*\b",           re.IGNORECASE | re.UNICODE),
    re.compile(r"\bлекарств\w*\b",          re.IGNORECASE | re.UNICODE),
    re.compile(r"\bпрепарат\w*\b",          re.IGNORECASE | re.UNICODE),
    re.compile(r"\bбеременн\w+\b",          re.IGNORECASE | re.UNICODE),
    re.compile(r"\bкорм(лю|ит)\s+грудью\b", re.IGNORECASE | re.UNICODE),
    re.compile(r"\bгрудно[йг]\s+ребен",     re.IGNORECASE | re.UNICODE),
]


# Operator-request keywords — explicit handover requests
OPERATOR_KEYWORDS: list[re.Pattern[str]] = [
    re.compile(r"\bоператор\w*\b",              re.IGNORECASE | re.UNICODE),
    re.compile(r"\bадминистратор\w*\b",         re.IGNORECASE | re.UNICODE),
    re.compile(r"(хочу|нужен|позови)\s+человек", re.IGNORECASE | re.UNICODE),
    re.compile(r"(говорить|пообщат)\s+с\s+юл",  re.IGNORECASE | re.UNICODE),
    re.compile(r"\bagent\b",                    re.IGNORECASE),
    re.compile(r"\bhuman\b",                    re.IGNORECASE),
]
```

### 2. Safety service

a) Создать `app/services/safety.py`:

```python
"""Safety filters for incoming and outgoing messages.

Two independent checks:

1. check_outgoing(text):
   Scans an outgoing reply (typically Claude-generated) for banned patterns.
   Used by smart scenario AFTER Claude returns text, BEFORE forwarding to provider.
   If matched: message is NOT sent, conversation goes to handover_pending,
   audit row written to messages with safety_blocked=True.

2. check_incoming(text):
   Quick triage of incoming messages. Detects:
   - Operator-request keywords ("оператор", "human") — explicit handover
   - Symptom keywords ("болит", "диагноз") — pre-emptive handover
     (cheaper than letting Claude tool-call escalate; protects against rare
     cases when Claude misjudges a medical question)
   Used by worker BEFORE scenario engine.

Trusted vs untrusted templates:
- Welcome / comment-to-DM templates from `scenarios.template` are TRUSTED:
  written by humans, reviewed, change rarely. NOT subjected to check_outgoing.
- Claude smart replies are UNTRUSTED: subject to check_outgoing.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.prompts.banned_patterns import (
    BANNED_PATTERNS,
    OPERATOR_KEYWORDS,
    SYMPTOM_KEYWORDS,
)
from app.utils.logging import get_logger

log = get_logger(__name__)

OutgoingVerdict = Literal["ok", "blocked"]
IncomingTrigger = Literal["none", "operator_request", "symptom"]


@dataclass(frozen=True)
class OutgoingCheck:
    verdict: OutgoingVerdict
    reason: str | None  # filled when verdict='blocked'


@dataclass(frozen=True)
class IncomingCheck:
    trigger: IncomingTrigger
    matched_text: str | None  # the snippet that triggered, for logging


def check_outgoing(text: str) -> OutgoingCheck:
    """Scan an outgoing reply for banned patterns.

    Returns:
        OutgoingCheck(verdict='ok') if clean
        OutgoingCheck(verdict='blocked', reason=label) on first match
    """
    if not text:
        return OutgoingCheck(verdict="ok", reason=None)
    for bp in BANNED_PATTERNS:
        if bp.pattern.search(text):
            log.warning(
                "safety_outgoing_blocked",
                pattern_label=bp.label,
                text_preview=text[:100],
            )
            return OutgoingCheck(verdict="blocked", reason=bp.label)
    return OutgoingCheck(verdict="ok", reason=None)


def check_incoming(text: str | None) -> IncomingCheck:
    """Triage an incoming message: does it require pre-emptive handover?

    Order of priority:
    1. Operator-request keyword → handover with explicit user-facing reply
    2. Symptom keyword → handover, optional silent acknowledgement
    3. None → let scenario engine route normally
    """
    if not text:
        return IncomingCheck(trigger="none", matched_text=None)

    for pattern in OPERATOR_KEYWORDS:
        m = pattern.search(text)
        if m:
            return IncomingCheck(trigger="operator_request", matched_text=m.group(0))

    for pattern in SYMPTOM_KEYWORDS:
        m = pattern.search(text)
        if m:
            return IncomingCheck(trigger="symptom", matched_text=m.group(0))

    return IncomingCheck(trigger="none", matched_text=None)
```

### 3. Notification bot

a) Расширить `app/config.py` — добавить два поля:

```python
# In Settings class:
    # --- Notification bot (admin alerts to Yulia) ---
    notification_bot_token: str = ""
    notification_admin_chat_id: int = 0
```

b) В `.env.example` добавить:

```bash
# --- Notification bot (admin alerts) ---
NOTIFICATION_BOT_TOKEN=
NOTIFICATION_ADMIN_CHAT_ID=
```

c) Создать `app/services/notifications.py`:

```python
"""Telegram notifications for admin (Yulia).

A standalone Telegram bot (separate from @yuliya_purify_bot) used solely for
operational alerts: handover events, blocked messages, errors.

Why separate from bot_purify:
- bot_purify talks to end-users; mixing admin and user channels is risky
  (e.g. accidentally posting an admin alert to a user)
- @BotFather setup is a one-time 2-minute job

If NOTIFICATION_BOT_TOKEN or NOTIFICATION_ADMIN_CHAT_ID is empty,
notifications are skipped silently with a log entry. Production must set both.
"""
from __future__ import annotations

import httpx

from app.config import get_settings
from app.utils.logging import get_logger

log = get_logger(__name__)

_TIMEOUT = httpx.Timeout(connect=2.0, read=5.0, write=2.0, pool=2.0)


async def notify_admin(text: str) -> bool:
    """Send a markdown-formatted message to admin chat.

    Returns True on success, False if config missing or send failed.
    Caller should NOT rely on this — admin notifications are best-effort.
    """
    settings = get_settings()
    if not settings.notification_bot_token or not settings.notification_admin_chat_id:
        log.info("notification_skipped_no_config", text_preview=text[:80])
        return False

    url = f"https://api.telegram.org/bot{settings.notification_bot_token}/sendMessage"
    payload = {
        "chat_id": settings.notification_admin_chat_id,
        "text": text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True,
    }

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            response = await client.post(url, json=payload)
    except httpx.HTTPError as exc:
        log.warning("notification_send_failed", error=str(exc))
        return False

    if response.status_code != 200:
        log.warning(
            "notification_telegram_error",
            status=response.status_code,
            body=response.text[:200],
        )
        return False

    return True
```

### 4. Handover service

a) Создать `app/services/handover.py`:

```python
"""Handover service — single point for transitioning a conversation
to handover_pending, with audit logging and admin notification.

Used by:
- safety.check_incoming → operator_request / symptom triggers
- smart scenario → Claude tool use 'escalate_to_human'
- safety check on outgoing Claude reply → banned pattern hit
- (future) admin manual handover from dashboard
"""
from __future__ import annotations

from typing import Literal

import asyncpg

from app.repos import conversations as conversations_repo
from app.services import notifications
from app.utils.logging import get_logger

log = get_logger(__name__)

HandoverSource = Literal[
    "operator_request",       # user wrote "оператор"
    "symptom_detected",       # incoming text matched symptom keyword
    "claude_tool_use",        # Claude requested escalate_to_human
    "outgoing_safety_block",  # Claude reply matched banned pattern
    "manual",                 # admin marked from dashboard (Task 15)
]


async def trigger_handover(
    *,
    conversation: asyncpg.Record,
    user: asyncpg.Record,
    source: HandoverSource,
    reason: str,
) -> None:
    """Transition a conversation to handover_pending and notify Yulia.

    Idempotent: calling twice on the same conversation is safe — status update
    is a simple UPDATE; second notification is sent (so Yulia sees the latest reason).
    """
    await conversations_repo.set_status(
        conversation["id"],
        "handover_pending",
        reason=f"{source}: {reason}",
    )
    log.info(
        "handover_triggered",
        conv_id=conversation["id"],
        user_id=user["id"],
        source=source,
        reason=reason,
    )

    # Best-effort admin notification
    msg = _format_admin_message(user=user, source=source, reason=reason)
    await notifications.notify_admin(msg)


def _format_admin_message(
    *,
    user: asyncpg.Record,
    source: HandoverSource,
    reason: str,
) -> str:
    """Format markdown text for admin Telegram notification."""
    username = user["username"] or "(no username)"
    full_name = user["full_name"] or "(no name)"
    platform = user["platform"]
    short_id = user["short_id"]

    source_label = {
        "operator_request": "👤 Запрос оператора",
        "symptom_detected": "⚠️ Симптомы / медицинский вопрос",
        "claude_tool_use": "🤖 Claude эскалировал",
        "outgoing_safety_block": "🛑 Заблокирован ответ (banned pattern)",
        "manual": "✋ Ручная эскалация",
    }.get(source, source)

    return (
        f"*{source_label}*\n\n"
        f"Платформа: `{platform}`\n"
        f"Пользователь: `@{username}` ({full_name})\n"
        f"short\\_id: `{short_id}`\n\n"
        f"*Причина:* {reason}\n\n"
        f"_Открой админку чтобы ответить._"
    )
```

### 5. Handover scenario для команды «оператор»

a) Создать `app/services/scenarios/handover.py`:

```python
"""Handover scenario — explicit user request for human operator.

Triggered by keyword like "оператор", "человек", "agent" (configured in DB).
Unlike Claude tool-use escalation (which silently flips status with no reply),
this scenario sends a polite ack to the user so they know help is coming.

Behavior:
1. Send polite acknowledgement: "Передаю Юле, она ответит в течение..."
2. Trigger handover (status flip + admin notification)
"""
from __future__ import annotations

import asyncpg

from app.models.events import IncomingEvent, OutgoingMessage
from app.services import handover
from app.services.scenario_engine import register_scenario
from app.utils.logging import get_logger

log = get_logger(__name__)


@register_scenario("handover")
async def handle_handover(
    event: IncomingEvent,
    user: asyncpg.Record,
    conversation: asyncpg.Record,
    scenario: asyncpg.Record,
) -> OutgoingMessage | None:
    template = scenario["template"] or (
        "Хорошо! Передаю Юле, она ответит лично в течение нескольких часов 💚"
    )

    # Trigger handover BEFORE sending — even if send fails, conversation
    # is still flagged for Yulia in admin.
    await handover.trigger_handover(
        conversation=conversation,
        user=user,
        source="operator_request",
        reason=event.text or "(empty user message)",
    )

    return OutgoingMessage(
        platform=event.platform,
        external_user_id=event.external_user_id,
        text=template,
        scenario_id=scenario["id"],
    )
```

### 6. Регистрация handover в __init__

a) Обновить `app/services/scenarios/__init__.py`:

```python
"""Scenario implementations.

Each scenario lives in its own module. Importing this package
triggers handler registration via the @register_scenario decorator.
"""
from app.services.scenarios import comment_to_dm  # noqa: F401
from app.services.scenarios import echo  # noqa: F401
from app.services.scenarios import handover  # noqa: F401
from app.services.scenarios import smart  # noqa: F401
from app.services.scenarios import welcome  # noqa: F401

__all__ = ["comment_to_dm", "echo", "handover", "smart", "welcome"]
```

### 7. Миграция: handover scenario + keyword «оператор»

a) Создать `migrations/011_seed_handover_scenario.sql`:

```sql
-- Migration 011: Handover scenario + global keyword for operator request.

INSERT INTO scenarios (name, type, template, metadata, active)
VALUES (
    'default_handover',
    'handover',
    E'Хорошо! 💚 Передаю Юле — она ответит лично в течение нескольких часов.\n\nЕсли вопрос срочный, напиши пожалуйста чем могу помочь дополнительно.',
    '{}'::jsonb,
    TRUE
)
ON CONFLICT (name) DO NOTHING;

-- Global keywords routing to handover scenario.
-- contains-match catches phrases like "хочу с оператором", "позовите человека"
INSERT INTO keywords (keyword, match_type, context, scenario_id, priority, case_sensitive, active)
SELECT 'оператор', 'contains', 'dm', s.id, 5, FALSE, TRUE
FROM scenarios s WHERE s.name = 'default_handover'
ON CONFLICT DO NOTHING;

INSERT INTO keywords (keyword, match_type, context, scenario_id, priority, case_sensitive, active)
SELECT 'администратор', 'contains', 'dm', s.id, 5, FALSE, TRUE
FROM scenarios s WHERE s.name = 'default_handover'
ON CONFLICT DO NOTHING;
```

   **Обоснование priority=5:** ниже всех остальных keywords (default 100, comment-to-DM 50). Когда пользователь написал «хочу очищение через оператора» — мы хотим routing на handover, не на purify. Низкое число = выше приоритет (см. KeywordMatcher из Task 07).

### 8. Per-user-day rate limit

a) Расширить `app/services/rate_limiter.py` — добавить:

```python
# Per-user daily reply cap (CLAUDE.md §12.4)
REPLIES_PER_DAY_LIMIT = 10
REPLIES_PER_DAY_WINDOW = 60 * 60 * 24  # 86400 seconds

# ...

async def can_reply_daily(user_id: int) -> bool:
    """Returns True if user is under daily reply limit.

    Rolling 24h window starting at first reply. Implementation: same INCR+EXPIRE
    as per-minute, just longer window. Approximate but cheap.
    """
    key = f"rl:reply:day:{user_id}"
    allowed = await check_and_increment(
        key, REPLIES_PER_DAY_LIMIT, REPLIES_PER_DAY_WINDOW,
    )
    if not allowed:
        log.warning("rate_limit_hit_replies_per_day", user_id=user_id)
    return allowed
```

b) Обновить `_send_and_record` в `app/workers/tasks_messages.py` — использовать оба лимита:

   Найти строку:
   ```python
   if not await can_reply(user_id):
   ```

   Заменить на:
   ```python
   from app.services.rate_limiter import can_reply, can_reply_daily

   if not await can_reply(user_id):
       log.warning("reply_throttled_per_minute", user_id=user_id)
       return
   if not await can_reply_daily(user_id):
       log.warning("reply_throttled_per_day", user_id=user_id)
       return
   ```

   Импорт уже есть — просто добавь `can_reply_daily` к нему.

### 9. Интеграция safety в worker (incoming check)

a) Обновить `app/workers/tasks_messages.py`. После шага «Insert incoming» (4) и до «Bump timestamps» (5) добавить incoming-safety check.

   Найти блок:

```python
        # 4. Insert incoming
        await messages.insert(
            conversation_id=conv["id"],
            direction="in",
            text=event.text,
            ...
        )

        # 5. Bump timestamps
        await users.update_last_message_at(user["id"], event.occurred_at)
        await conversations.update_last_message_at(conv["id"], event.occurred_at)

        # 6. Scenario engine
```

   Заменить на:

```python
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

        # 5b. Pre-emptive safety triage on incoming text
        # (skipped for comments — operator-request keyword in a public Reels comment
        #  is unusual; we let comment-to-DM scenario handle it normally)
        if event.event_type == "message" and event.text:
            safety_check = safety.check_incoming(event.text)
            if safety_check.trigger == "symptom":
                log.info(
                    "incoming_symptom_detected",
                    user_id=user["id"],
                    matched=safety_check.matched_text,
                )
                await handover.trigger_handover(
                    conversation=conv, user=user,
                    source="symptom_detected",
                    reason=f"matched: {safety_check.matched_text}",
                )
                # Do NOT engage scenario engine — Yulia takes over
                await events_repo.mark_processed(log_id, error=None)
                return
            # Note: operator_request goes through scenario engine →
            # keyword "оператор" → handover scenario. Don't shortcut here —
            # we want the polite ack message sent to the user.

        # 6. Scenario engine
```

   Импорты в начале файла:
   ```python
   from app.services import handover, safety
   ```

### 10. Интеграция safety в smart scenario (outgoing check)

a) Обновить `app/services/scenarios/smart.py`. После того как Claude вернул `reply.text`, ПЕРЕД построением `OutgoingMessage`, прогнать через `check_outgoing`.

   Найти блок (внутри `handle_smart`):

```python
    # Build outgoing message. Note: ...
    return OutgoingMessage(
        platform=event.platform,
        external_user_id=event.external_user_id,
        text=reply.text,
        scenario_id=scenario["id"],
        claude_metadata={...},
    )
```

   Заменить на:

```python
    # Outgoing safety check on Claude's reply
    if reply.text:
        safety_result = safety.check_outgoing(reply.text)
        if safety_result.verdict == "blocked":
            log.warning(
                "smart_blocked_by_safety",
                user_id=user["id"],
                reason=safety_result.reason,
                text_preview=reply.text[:120],
            )
            # Persist audit row and trigger handover
            await messages_repo.insert(
                conversation_id=conversation["id"],
                direction="out",
                text=None,                       # not delivered
                source="reply",
                scenario_id=scenario["id"],
                claude_used=True,
                claude_model=reply.model,
                claude_tokens_in=reply.tokens_in,
                claude_tokens_out=reply.tokens_out,
                safety_blocked=True,
                safety_reason=safety_result.reason,
                external_message_id=f"blocked:{user['id']}:{reply.tokens_out}",
            )
            await handover.trigger_handover(
                conversation=conversation, user=user,
                source="outgoing_safety_block",
                reason=safety_result.reason or "unknown",
            )
            return None

    return OutgoingMessage(
        platform=event.platform,
        external_user_id=event.external_user_id,
        text=reply.text,
        scenario_id=scenario["id"],
        claude_metadata={
            "model": reply.model,
            "tokens_in": reply.tokens_in,
            "tokens_out": reply.tokens_out,
        },
    )
```

   Импорты в начале файла smart.py:
   ```python
   from app.repos import messages as messages_repo
   from app.services import handover, safety
   ```

   Также обнови старый блок про escalation:

   Найти:
```python
    if reply.escalation:
        await conversations_repo.set_status(
            conversation["id"],
            "handover_pending",
            reason=f"claude: {reply.escalation_reason}",
        )
        ...
```

   Заменить на:
```python
    if reply.escalation:
        await handover.trigger_handover(
            conversation=conversation, user=user,
            source="claude_tool_use",
            reason=reply.escalation_reason or "no reason given",
        )
        log.info(
            "smart_escalated_to_human",
            user_id=user["id"],
            conv_id=conversation["id"],
            reason=reply.escalation_reason,
        )
        return None
```

   И убрать импорт `conversations_repo` если больше не используется (handover service делает это сам).

### 11. Тесты

a) Создать `tests/test_safety.py`:

```python
"""Tests for safety filters: banned patterns, symptom detection, operator keywords."""
from __future__ import annotations

import pytest

from app.services.safety import check_incoming, check_outgoing


# ---- Outgoing: banned medical claims ----

@pytest.mark.parametrize("phrase, expected_label_substr", [
    ("Это масло лечит головную боль", "лечит"),
    ("Лаванда вылечит бессонницу", "вылечит"),
    ("Профилактика рака с маслами", "профилактика"),
    ("Замените лекарства на масла", "замена лекарств"),
    ("Можно вместо таблеток", "вместо таблеток"),
    ("Гарантирую результат за неделю", "гарантирую"),
    ("100% результат через месяц", "100"),
    ("Эфирные масла как антибиотики", "антибиотик"),
    ("Можно принимать внутрь без консультации", "опасное"),
    ("У вас диагноз — стресс", "диагностика"),
])
def test_outgoing_blocks_medical_claims(phrase: str, expected_label_substr: str) -> None:
    result = check_outgoing(phrase)
    assert result.verdict == "blocked", f"Should block: {phrase!r}"
    assert expected_label_substr.lower() in result.reason.lower()


@pytest.mark.parametrize("phrase", [
    "Программа очищения — это 30 дней с маслами",
    "Эфирные масла doTERRA имеют сертификат CPTG",
    "Лаванда часто помогает расслабиться",  # 'помогает' alone is fine
    "Многие используют масла для ухода за кожей",
    "Юля проводит онлайн-консультации",
    "В программу входят масла и бады",
])
def test_outgoing_allows_safe_phrases(phrase: str) -> None:
    result = check_outgoing(phrase)
    assert result.verdict == "ok", f"Should pass: {phrase!r}, got: {result.reason}"


def test_outgoing_empty_text_is_ok() -> None:
    assert check_outgoing("").verdict == "ok"


# ---- Incoming: operator request ----

@pytest.mark.parametrize("phrase", [
    "Хочу с оператором поговорить",
    "Позовите человека пожалуйста",
    "Можно с Юлей лично пообщаться?",
    "I want to talk to a human",
    "Need an agent",
    "Нужен администратор",
])
def test_incoming_detects_operator_request(phrase: str) -> None:
    result = check_incoming(phrase)
    assert result.trigger == "operator_request", f"Failed for: {phrase!r}"


# ---- Incoming: symptoms ----

@pytest.mark.parametrize("phrase", [
    "У меня болит голова",
    "Боль в спине, что взять?",
    "Мне поставили диагноз диабет",
    "Я беременна, можно ли масла?",
    "Кормлю грудью, какие масла безопасны?",
    "Был у врача, прописали лекарства",
    "Принимаю таблетки от давления",
])
def test_incoming_detects_symptom(phrase: str) -> None:
    result = check_incoming(phrase)
    assert result.trigger == "symptom", f"Failed for: {phrase!r}"


@pytest.mark.parametrize("phrase", [
    "Расскажи про программу",
    "Какие масла любимые?",
    "Хочу попробовать очищение",
    "Сколько стоят пробники?",
])
def test_incoming_passes_normal_questions(phrase: str) -> None:
    result = check_incoming(phrase)
    assert result.trigger == "none", f"False positive on: {phrase!r}"


def test_incoming_empty_text() -> None:
    assert check_incoming(None).trigger == "none"
    assert check_incoming("").trigger == "none"


def test_operator_request_priority_over_symptom() -> None:
    """If both keywords present, operator_request wins (we want to ack the user)."""
    result = check_incoming("Хочу к оператору, у меня болит спина")
    assert result.trigger == "operator_request"
```

b) Создать `tests/test_notifications.py`:

```python
"""Tests for admin notifications (mocked Telegram API)."""
from __future__ import annotations

import os

import httpx
import pytest

from app.services import notifications


@pytest.mark.asyncio
async def test_notify_admin_skips_when_no_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NOTIFICATION_BOT_TOKEN", "")
    monkeypatch.setenv("NOTIFICATION_ADMIN_CHAT_ID", "0")
    from app.config import get_settings
    get_settings.cache_clear()

    result = await notifications.notify_admin("test message")
    assert result is False


@pytest.mark.asyncio
async def test_notify_admin_sends_when_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NOTIFICATION_BOT_TOKEN", "fake-token")
    monkeypatch.setenv("NOTIFICATION_ADMIN_CHAT_ID", "12345")
    from app.config import get_settings
    get_settings.cache_clear()

    captured: list[dict] = []

    class FakeResponse:
        def __init__(self) -> None:
            self.status_code = 200
            self.text = "ok"

    class FakeClient:
        def __init__(self, *args, **kwargs) -> None: pass
        async def __aenter__(self) -> "FakeClient": return self
        async def __aexit__(self, *args) -> None: pass
        async def post(self, url: str, json: dict) -> FakeResponse:
            captured.append({"url": url, "json": json})
            return FakeResponse()

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)

    result = await notifications.notify_admin("Hello *Yulia*")

    assert result is True
    assert len(captured) == 1
    assert "fake-token" in captured[0]["url"]
    assert captured[0]["json"]["chat_id"] == 12345
    assert captured[0]["json"]["text"] == "Hello *Yulia*"
    assert captured[0]["json"]["parse_mode"] == "Markdown"


@pytest.mark.asyncio
async def test_notify_admin_returns_false_on_http_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NOTIFICATION_BOT_TOKEN", "fake-token")
    monkeypatch.setenv("NOTIFICATION_ADMIN_CHAT_ID", "12345")
    from app.config import get_settings
    get_settings.cache_clear()

    class FakeClient:
        def __init__(self, *args, **kwargs) -> None: pass
        async def __aenter__(self) -> "FakeClient": return self
        async def __aexit__(self, *args) -> None: pass
        async def post(self, *args, **kwargs):
            raise httpx.ConnectError("network down")

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)

    result = await notifications.notify_admin("test")
    assert result is False
```

c) Создать `tests/test_handover_service.py`:

```python
"""Tests for handover service: status flip + admin notification."""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.repos import conversations, users
from app.services import handover, notifications


@pytest.mark.asyncio
async def test_trigger_handover_flips_status(
    db, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(notifications, "notify_admin", AsyncMock(return_value=True))

    user = await users.create(
        provider_name="sendpulse", platform="instagram",
        external_id="ho_user_1", username="anna", full_name="Anna",
    )
    conv = await conversations.create(user["id"], "instagram")

    await handover.trigger_handover(
        conversation=conv, user=user,
        source="operator_request",
        reason="user typed 'оператор'",
    )

    updated = await db.fetchrow(
        "SELECT status, handover_reason FROM conversations WHERE id = $1",
        conv["id"],
    )
    assert updated["status"] == "handover_pending"
    assert "operator_request" in updated["handover_reason"]


@pytest.mark.asyncio
async def test_trigger_handover_calls_notify_admin(
    db, monkeypatch: pytest.MonkeyPatch,
) -> None:
    notify_mock = AsyncMock(return_value=True)
    monkeypatch.setattr(notifications, "notify_admin", notify_mock)

    user = await users.create(
        provider_name="sendpulse", platform="instagram",
        external_id="ho_notify", username="bob", full_name="Bob Smith",
    )
    conv = await conversations.create(user["id"], "instagram")

    await handover.trigger_handover(
        conversation=conv, user=user,
        source="symptom_detected",
        reason="болит",
    )

    notify_mock.assert_called_once()
    sent_text = notify_mock.call_args.args[0]
    assert "Симптомы" in sent_text or "симптом" in sent_text.lower()
    assert "bob" in sent_text
    assert user["short_id"] in sent_text


@pytest.mark.asyncio
async def test_trigger_handover_idempotent(
    db, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(notifications, "notify_admin", AsyncMock(return_value=True))

    user = await users.create(
        provider_name="sendpulse", platform="instagram",
        external_id="ho_idem",
    )
    conv = await conversations.create(user["id"], "instagram")

    await handover.trigger_handover(
        conversation=conv, user=user, source="operator_request", reason="r1",
    )
    # Second call doesn't crash
    await handover.trigger_handover(
        conversation=conv, user=user, source="operator_request", reason="r2",
    )

    updated = await db.fetchrow(
        "SELECT handover_reason FROM conversations WHERE id = $1",
        conv["id"],
    )
    # Latest reason wins
    assert "r2" in updated["handover_reason"]
```

d) Создать `tests/test_scenario_handover.py`:

```python
"""Tests for handover scenario handler (keyword 'оператор')."""
from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from app.models.events import IncomingEvent
from app.repos import conversations, scenarios as scenarios_repo, users
from app.services import notifications
from app.services.scenarios.handover import handle_handover


@pytest.mark.asyncio
async def test_handover_returns_polite_ack(
    db, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(notifications, "notify_admin", AsyncMock(return_value=True))

    user = await users.create(
        provider_name="sendpulse", platform="instagram",
        external_id="hsc_user_1",
    )
    conv = await conversations.create(user["id"], "instagram")
    scenario = await scenarios_repo.get_by_name("default_handover")
    assert scenario is not None

    event = IncomingEvent(
        provider="sendpulse",
        platform="instagram",
        event_type="message",
        external_user_id="hsc_user_1",
        external_event_id="evt_h_1",
        text="Хочу к оператору",
        occurred_at=datetime.now(UTC),
    )

    msg = await handle_handover(event, user, conv, scenario)

    assert msg is not None
    assert "Юле" in msg.text or "юле" in msg.text.lower()

    updated = await db.fetchrow(
        "SELECT status FROM conversations WHERE id = $1", conv["id"],
    )
    assert updated["status"] == "handover_pending"
```

e) Создать `tests/test_e2e_handover_pipeline.py`:

```python
"""E2E test: incoming with symptom keyword → pre-emptive handover, no Claude call.

Most important test of Task 14 — proves that the safety net works
even if Claude API is unreachable.
"""
from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import AsyncClient

from app.models.events import IncomingEvent
from app.repos import events as events_repo, users
from app.services import claude_responder, notifications
from app.workers.tasks_messages import process_incoming_event
from tests.fakes.fake_provider import FakeProvider


@pytest.mark.asyncio
async def test_symptom_message_triggers_handover_without_claude(
    client: AsyncClient,
    fake_provider: FakeProvider,
    db,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    notify_mock = AsyncMock(return_value=True)
    monkeypatch.setattr(notifications, "notify_admin", notify_mock)

    # If Claude is called, fail loudly — symptom should pre-empt
    fake_anthropic = MagicMock()
    fake_anthropic.messages.create = AsyncMock(
        side_effect=AssertionError("Claude must NOT be called for symptom messages"),
    )
    monkeypatch.setattr(claude_responder, "_client", fake_anthropic)

    event = IncomingEvent(
        provider="sendpulse",
        platform="instagram",
        event_type="message",
        external_user_id="symptom_user",
        external_event_id="symp_evt_1",
        username="anna",
        full_name="Anna P",
        text="у меня болит голова, что использовать?",
        occurred_at=datetime(2026, 5, 8, 12, 0, tzinfo=UTC),
    )
    log_row = await events_repo.insert(
        provider_name=event.provider, platform=event.platform,
        event_type=event.event_type, external_event_id=event.external_event_id,
        payload={}, signature_valid=True,
    )

    await process_incoming_event({}, event.model_dump(mode="json"), log_row["id"])

    # Conversation flipped
    user = await users.get_by_external("sendpulse", "instagram", "symptom_user")
    conv = await db.fetchrow(
        "SELECT * FROM conversations WHERE user_id = $1", user["id"],
    )
    assert conv["status"] == "handover_pending"
    assert "symptom_detected" in (conv["handover_reason"] or "")

    # No outgoing message sent
    assert len(fake_provider.sent) == 0

    # Admin notified
    notify_mock.assert_called_once()


@pytest.mark.asyncio
async def test_operator_keyword_routes_through_handover_scenario(
    client: AsyncClient,
    fake_provider: FakeProvider,
    db,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Different from symptom flow: user gets a polite reply."""
    monkeypatch.setattr(notifications, "notify_admin", AsyncMock(return_value=True))

    event = IncomingEvent(
        provider="sendpulse",
        platform="instagram",
        event_type="message",
        external_user_id="operator_user",
        external_event_id="op_evt_1",
        full_name="Anna",
        text="хочу оператора, у меня вопрос",
        occurred_at=datetime(2026, 5, 8, 12, 0, tzinfo=UTC),
    )
    log_row = await events_repo.insert(
        provider_name=event.provider, platform=event.platform,
        event_type=event.event_type, external_event_id=event.external_event_id,
        payload={}, signature_valid=True,
    )

    await process_incoming_event({}, event.model_dump(mode="json"), log_row["id"])

    # Polite ack sent
    assert len(fake_provider.sent) == 1
    assert "Юле" in fake_provider.sent[0].text

    # Status flipped
    user = await users.get_by_external("sendpulse", "instagram", "operator_user")
    conv = await db.fetchrow(
        "SELECT * FROM conversations WHERE user_id = $1", user["id"],
    )
    assert conv["status"] == "handover_pending"


@pytest.mark.asyncio
async def test_outgoing_safety_blocks_claude_with_medical_claim(
    client: AsyncClient,
    fake_provider: FakeProvider,
    db,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Claude returns medical claim → safety blocks → no message sent + handover."""
    monkeypatch.setattr(notifications, "notify_admin", AsyncMock(return_value=True))

    # Pre-create user (so they're not "new" → smart fallback fires)
    user = await users.create(
        provider_name="sendpulse", platform="instagram",
        external_id="safety_block_user", full_name="Anna",
    )

    # Stub Claude to return a medical claim
    from dataclasses import dataclass
    @dataclass
    class _Usage: input_tokens: int = 50; output_tokens: int = 30
    @dataclass
    class _Text: type: str = "text"; text: str = ""
    @dataclass
    class _Resp: content: list = None; usage: _Usage = None

    bad_response = _Resp(
        content=[_Text(text="Конечно! Это масло вылечит ваш недуг 🌿")],
        usage=_Usage(),
    )

    fake_anthropic = MagicMock()
    fake_anthropic.messages.create = AsyncMock(return_value=bad_response)
    monkeypatch.setattr(claude_responder, "_client", fake_anthropic)

    # Mark welcome already sent so engine fallback hits smart, not welcome
    from app.services import lead_tracker
    await lead_tracker.mark_welcome_sent(user["id"])

    event = IncomingEvent(
        provider="sendpulse",
        platform="instagram",
        event_type="message",
        external_user_id="safety_block_user",
        external_event_id="sb_evt_1",
        text="расскажи про масла",  # benign — passes incoming check
        occurred_at=datetime(2026, 5, 8, 12, 0, tzinfo=UTC),
    )
    log_row = await events_repo.insert(
        provider_name=event.provider, platform=event.platform,
        event_type=event.event_type, external_event_id=event.external_event_id,
        payload={}, signature_valid=True,
    )

    await process_incoming_event({}, event.model_dump(mode="json"), log_row["id"])

    # Nothing sent
    assert len(fake_provider.sent) == 0

    # Audit row exists with safety_blocked=True
    blocked_msg = await db.fetchrow(
        """
        SELECT * FROM messages
        WHERE conversation_id IN (SELECT id FROM conversations WHERE user_id = $1)
          AND safety_blocked = TRUE
        """,
        user["id"],
    )
    assert blocked_msg is not None
    assert "вылечит" in (blocked_msg["safety_reason"] or "")
    assert blocked_msg["text"] is None  # never delivered

    # Conversation in handover
    conv = await db.fetchrow(
        "SELECT * FROM conversations WHERE user_id = $1", user["id"],
    )
    assert conv["status"] == "handover_pending"
    assert "outgoing_safety_block" in (conv["handover_reason"] or "")
```

---

## Acceptance criteria

- [ ] Файлы созданы по структуре подзадач 1–10
- [ ] Миграция 011 применена: `SELECT name FROM scenarios WHERE name='default_handover'` возвращает строку
- [ ] Keyword «оператор» посеян: `SELECT keyword, priority FROM keywords WHERE keyword='оператор'` показывает priority=5
- [ ] `make lint` проходит
- [ ] `make test` проходит, все новые тесты зелёные:
  - `test_safety.py` — 30+ тестов (parametrize)
  - `test_notifications.py` — 3 теста
  - `test_handover_service.py` — 3 теста
  - `test_scenario_handover.py` — 1 тест
  - `test_e2e_handover_pipeline.py` — 3 ключевых теста
  - Все существующие тесты Tasks 01, 03, 04, 06, 07, 08, 09, 11, 13 продолжают работать
- [ ] **Самый важный тест** `test_outgoing_safety_blocks_claude_with_medical_claim` зелёный — это юридический safeguard
- [ ] При наличии `NOTIFICATION_BOT_TOKEN` и `NOTIFICATION_ADMIN_CHAT_ID` в `.env`:
  - Запустить через docker compose
  - В тестовом DM написать «у меня болит голова»
  - Юля получает уведомление в Telegram через notification bot
- [ ] Без credentials notification bot — handover отрабатывает, но Юля просто не получает Telegram-уведомление; в логах `notification_skipped_no_config`

---

## Do NOT

- НЕ применять `check_outgoing` к шаблонам welcome / comment-to-DM / handover. Они доверенные. Только Claude-ответы проверяются.
- НЕ удалять заблокированные сообщения из БД. Их `safety_blocked=True, text=NULL` — это ценный аудит-trail для compliance-аудита и для улучшения промптов.
- НЕ отправлять пользователю «извините, ваш ответ заблокирован». Это раскрывает механизм. Просто молчание + handover к Юле, она ответит вручную.
- НЕ использовать Claude для классификации medical claims. Регэксп быстрее, дешевле, детерминирован, не galлюцинирует.
- НЕ ставить keyword «оператор» с `priority=100`. priority=5 даёт ему выигрыш при конфликте с другими keywords (например, «хочу очищение через оператора»).
- НЕ добавлять keyword «оператор» с context='comment'. Это DM-команда, не комментарий под Reels.
- НЕ хранить notification_bot_token в БД. Только в `.env`.
- НЕ делать notification synchronous-blocking в worker. Best-effort, fire-and-forget с timeout 5с.
- НЕ создавать новый Telegram bot из кода (через @BotFather). Юля создаёт вручную и передаёт токен.
- НЕ обнулять conversations.status='handover_pending' автоматически. Только Юля или админ через дашборд (Task 15).

---

## Зависимости задачи

- Tasks 01, 03, 04, 06, 07, 08, 09, 11, 13 применены
- Юля создаёт notification bot через @BotFather (5 минут):
  1. Открыть `@BotFather` в Telegram → `/newbot`
  2. Назвать «Yulia Inbox Notifier» (или похоже)
  3. Получить токен → положить в `.env` как `NOTIFICATION_BOT_TOKEN`
  4. Найти бота в Telegram, написать `/start` → этот шаг обязателен иначе бот не сможет ей написать
  5. Открыть `https://api.telegram.org/bot<TOKEN>/getUpdates` → найти `chat.id` → положить как `NOTIFICATION_ADMIN_CHAT_ID`
- Production: Tasks 14 + 05 = функциональная готовность к запуску

---

## Что после этой задачи

После применения Task 14:

```
✅ doTERRA compliance: regex-фильтры на medical claims в Claude-ответах
✅ Pre-emptive handover: симптомы / беременность → сразу к Юле
✅ Explicit handover: «оператор» → polite ack + переключение
✅ Admin notifications: Юля видит handover в Telegram через минуту
✅ Per-user-day limits: 10 ответов в сутки
✅ Audit trail: safety_blocked сообщения остаются в БД
```

Дальше:

- **Task 05** — SendPulseProvider implementation (требует Юлины credentials)
- **Task 15** — Admin dashboard: Юля управляет handover-ами, смотрит метрики
- **Task 16** — Monitoring (Sentry, healthcheck с heartbeat из Task 06)
- **Task 17** — Production deployment
- **Task 18** — Smoke tests + go-live

После Task 05 + 17 — система **развёрнута и работает в проде**.

---

**Дата создания:** 2026-05-08
**Применять в:** `D:\Work\social_inbox` после Tasks 01, 03, 04, 06, 07, 08, 09, 11, 13
**Эстимейт:** 5–6 часов на Claude Code + ручная проверка
