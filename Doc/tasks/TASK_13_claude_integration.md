# Task 13: Claude integration — smart replies

> Применить в `D:\Work\social_inbox` после Tasks 01, 03, 04, 06, 07, 08, 09, 11. Положить файл в корень проекта, открыть в VS Code, попросить Claude Code: «Прочитай TASK_13_claude_integration.md и выполни все подзадачи последовательно. Перед каждой подзадачей кратко опиши план, после — что фактически сделано».

---

## Контекст

После Tasks 08–11 у нас работает:
- Welcome при первом DM
- Comment-to-DM на keyword
- Echo fallback на всё остальное (тестовый stub)

Echo пора убрать. Когда подписчик уже получил welcome (или comment-to-DM) и продолжает писать в DM с дополнительными вопросами («а сколько стоит?», «есть ли пробники?», «что входит в программу?») — нужен осмысленный ответ.

Здесь подключается Claude.

**Архитектурно:** `EchoScenario` заменяется на `SmartScenario` типа `'smart'`, который вызывает Claude API. Engine fallback переключается с `echo_scenario` на `default_smart`. EchoScenario остаётся как класс для тестов и совместимости — миграция БД меняет только данные, не схему.

**Важное ограничение doTERRA-compliance:** Claude не должен давать медицинских советов и обещаний. Защита через:
- System prompt с явными границами
- Tool use: `escalate_to_human(reason)` для случаев «пользователь спрашивает про симптомы»
- Safety-фильтры на исходящие сообщения — заложены в Task 14, но в Task 13 место для них уже подготовлено.

---

## Цель

После выполнения этой задачи:

- `app/services/claude_responder.py` — обёртка над Anthropic SDK с tool use, контекстом, бюджетом токенов
- `app/prompts/system_smart.md` — system prompt в отдельном файле, кэшируется при загрузке
- `app/services/scenarios/smart.py` — scenario handler типа `'smart'`, использует claude_responder
- Tool `escalate_to_human(reason)` корректно ставит conversation в handover_pending
- Миграция: создан scenario `default_smart`, fallback в engine переключен с `echo_scenario` на `default_smart`
- Per-user-day бюджет токенов: 50k input + 10k output. При превышении — None (молчание + warning в логах)
- Контекст: последние 20 сообщений conversation, в chronological order
- Защита от prompt injection: user-сообщения оборачиваются в `<user_message>...</user_message>`
- Записываем `claude_tokens_in`, `claude_tokens_out`, `claude_model` в `messages`
- Тесты с моком Anthropic SDK покрывают: успешный ответ, escalation tool, превышение бюджета, обработка ошибок API
- E2E через FakeProvider + замоканный Anthropic: webhook → smart scenario → Claude → ответ

---

## Подзадачи

### 1. System prompt в отдельном файле

a) Создать `app/prompts/system_smart.md`:

```markdown
Ты — автоматический помощник Юлии (@yulia_purify), консультанта doTERRA по эфирным маслам и программе «Очищение». Ты общаешься с подписчиками Юлии в Instagram Direct и Facebook Messenger.

# Твоя задача

Отвечать на простые вопросы подписчиков дружелюбно и кратко (2–4 предложения). Если вопрос требует личной консультации, диагностики или подбора масел под человека — направлять на личное общение с Юлией через Telegram-бот или через эскалацию.

# Тон общения

- Дружеский, на «ты», тёплый
- Без формальностей, как подруга, которая разбирается в теме
- Можно использовать эмодзи умеренно (1–2 в сообщении): 🌿 💚 ✨ 🌸
- Без капса, без агрессивных продаж

# Что МОЖНО говорить

- Общая информация о программе «Очищение»: это 30-дневная программа на основе натуральных эфирных масел doTERRA, которая помогает мягко очистить организм
- Базовая информация про эфирные масла doTERRA: высокое качество (CPTG-сертификация), сертификат чистоты на каждый флакон
- Цены и условия можно обсудить лично — приглашай в Telegram (@yuliya_purify_bot) или к Юлии напрямую
- Стандартные вопросы про доставку, как заказать — можно ответить в общих чертах и направить в Telegram за деталями

# Что КАТЕГОРИЧЕСКИ НЕЛЬЗЯ

- НЕ давать медицинских советов и не делать заявлений о лечении или профилактике конкретных заболеваний
- НЕ обещать конкретных результатов («поможет», «вылечит», «избавит от...»)
- НЕ давать инструкций по применению масел внутрь без консультации специалиста
- НЕ заменять масла на лекарства, не отговаривать от приёма прописанных препаратов
- НЕ говорить, что масла — это альтернатива врачу или антибиотикам
- НЕ диагностировать симптомы пользователя

# Когда ОБЯЗАТЕЛЬНО эскалировать на человека

Используй tool `escalate_to_human` с понятной причиной если:

1. Пользователь жалуется на симптомы или болезнь (боли, диагноз, лечение, побочные эффекты)
2. Пользователь беременна или кормит грудью и спрашивает про масла
3. Пользователь спрашивает про детей младше 6 лет
4. Пользователь явно просит поговорить с Юлей лично («хочу с Юлей», «оператор», «человек», «agent»)
5. Сложные многоэтапные вопросы про подбор персональной программы
6. Пользователь раздражён, грубит, пишет жалобу

После эскалации НЕ отправляй пользователю никакого ответа — Юля сама ответит.

# Когда уместно дать ссылку на Telegram-бот

Если вопрос требует подробного ответа или ты хочешь продолжить разговор удобнее, можешь предложить перейти в @yuliya_purify_bot. Но не вставляй ссылку в каждый ответ — только когда это уместно.

# Формат ответа

Кратко, по делу, без воды. Если можешь ответить в одном предложении — отвечай в одном предложении. Не пиши длинных простыней.
```

### 2. Расширение repos/messages — token usage tracking

a) Создать новый репозиторий `app/repos/token_budget.py` для учёта расхода токенов:

```python
"""Per-user-day Claude token budget tracking via Redis.

Limits (in line with CLAUDE.md §12.4 spirit, per-user defensive budget):
- input tokens: 50,000 per UTC day
- output tokens: 10,000 per UTC day

Why per-day, not per-month:
- Cheap protection against abuse (one user spamming long messages)
- Resets daily — doesn't permanently lock out a real user

Why Redis, not DB:
- High-frequency hot path (every Claude call)
- Auto-expiring keys at end of UTC day
- DB is the audit trail (messages.claude_tokens_in/out), Redis is the gate
"""
from __future__ import annotations

from datetime import UTC, datetime

from app.repos.redis_client import get_redis
from app.utils.logging import get_logger

log = get_logger(__name__)

INPUT_BUDGET_PER_DAY = 50_000
OUTPUT_BUDGET_PER_DAY = 10_000

# TTL: just over 24h to handle clock drift; key includes UTC date so old keys naturally don't collide.
KEY_TTL_SECONDS = 60 * 60 * 26


def _today_utc() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d")


def _input_key(user_id: int) -> str:
    return f"claude:budget:in:{user_id}:{_today_utc()}"


def _output_key(user_id: int) -> str:
    return f"claude:budget:out:{user_id}:{_today_utc()}"


async def can_call_claude(user_id: int) -> bool:
    """Return True if the user has not exceeded their daily token budget.

    Checked BEFORE issuing the API call. We can't know exact token counts in advance,
    but we know what's already been spent today.
    """
    redis = await get_redis()
    in_used = int(await redis.get(_input_key(user_id)) or 0)
    out_used = int(await redis.get(_output_key(user_id)) or 0)

    if in_used >= INPUT_BUDGET_PER_DAY or out_used >= OUTPUT_BUDGET_PER_DAY:
        log.warning(
            "claude_budget_exceeded",
            user_id=user_id,
            in_used=in_used,
            out_used=out_used,
            in_limit=INPUT_BUDGET_PER_DAY,
            out_limit=OUTPUT_BUDGET_PER_DAY,
        )
        return False
    return True


async def record_usage(user_id: int, tokens_in: int, tokens_out: int) -> None:
    """Increment the user's token counters. Called AFTER each Claude API call."""
    redis = await get_redis()
    pipe = redis.pipeline()
    pipe.incrby(_input_key(user_id), tokens_in)
    pipe.expire(_input_key(user_id), KEY_TTL_SECONDS)
    pipe.incrby(_output_key(user_id), tokens_out)
    pipe.expire(_output_key(user_id), KEY_TTL_SECONDS)
    await pipe.execute()
```

### 3. ClaudeResponder service

a) Создать `app/services/claude_responder.py`:

```python
"""Claude API wrapper for smart-mode replies.

Responsibilities:
- Build messages array from conversation history
- Wrap user content in <user_message> tags (prompt injection defense)
- Call Anthropic API with tool use enabled
- Detect escalate_to_human tool calls
- Track token usage in Redis budget + DB messages row
- Handle API errors gracefully (return None on failure, never raise to caller)

Returns:
- ClaudeReply with text and metadata on successful response
- ClaudeReply with escalation=True (no text) when Claude requested handover
- None on failure / over-budget / no content (caller should fall back gracefully)
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from anthropic import AsyncAnthropic
from anthropic._exceptions import APIError, APIStatusError

from app.config import get_settings
from app.repos import token_budget
from app.repos import messages as messages_repo
from app.utils.logging import get_logger

if TYPE_CHECKING:
    import asyncpg

log = get_logger(__name__)

PROMPT_PATH = Path(__file__).resolve().parents[1] / "prompts" / "system_smart.md"

# Cache the prompt at module load — it's static, no need to re-read.
_SYSTEM_PROMPT_CACHE: str | None = None


def _load_system_prompt() -> str:
    global _SYSTEM_PROMPT_CACHE
    if _SYSTEM_PROMPT_CACHE is None:
        _SYSTEM_PROMPT_CACHE = PROMPT_PATH.read_text(encoding="utf-8").strip()
    return _SYSTEM_PROMPT_CACHE


# Tool definitions for Claude
TOOLS: list[dict[str, Any]] = [
    {
        "name": "escalate_to_human",
        "description": (
            "Передай разговор живому оператору (Юле). "
            "Используй когда: пользователь жалуется на симптомы/болезнь; "
            "пользователь беременна; вопрос про детей до 6 лет; "
            "пользователь явно просит человека/оператора; "
            "сложный персональный вопрос; пользователь раздражён."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "reason": {
                    "type": "string",
                    "description": "Краткая причина эскалации (1-2 предложения, на русском).",
                }
            },
            "required": ["reason"],
        },
    }
]


@dataclass(frozen=True)
class ClaudeReply:
    """Result of a Claude API call."""
    text: str | None
    escalation: bool
    escalation_reason: str | None
    tokens_in: int
    tokens_out: int
    model: str


_client: AsyncAnthropic | None = None


def _get_client() -> AsyncAnthropic:
    global _client
    if _client is None:
        settings = get_settings()
        _client = AsyncAnthropic(api_key=settings.anthropic_api_key)
    return _client


def reset_client() -> None:
    """Reset the Anthropic client. Tests use this with monkeypatch."""
    global _client
    _client = None


async def respond(
    *,
    user_id: int,
    conversation_id: int,
    incoming_text: str,
    model: str | None = None,
    max_tokens: int = 500,
) -> ClaudeReply | None:
    """Generate a smart reply via Claude API.

    Args:
        user_id: social_users.id, used for token budget tracking
        conversation_id: conversations.id, used to load history
        incoming_text: the latest user message (just inserted into messages table)
        model: Claude model identifier (defaults to settings.claude_default_model)
        max_tokens: cap on output length

    Returns:
        ClaudeReply on success.
        None if budget exceeded, API error, empty response, or no usable content.
    """
    settings = get_settings()
    chosen_model = model or settings.claude_default_model

    # Budget gate
    if not await token_budget.can_call_claude(user_id):
        return None

    # Load conversation context (last 20 messages, chronological)
    history = await messages_repo.get_recent(conversation_id, limit=20)

    # Build Anthropic messages array
    api_messages = _build_messages(history, latest_user_text=incoming_text)
    system_prompt = _load_system_prompt()

    client = _get_client()
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
        return None
    except APIError as exc:
        log.warning(
            "claude_api_error",
            user_id=user_id,
            error=str(exc)[:200],
        )
        return None
    except Exception as exc:
        # Defensive: any unexpected failure must not crash the worker.
        log.exception("claude_unexpected_error", user_id=user_id, error=str(exc))
        return None

    tokens_in = response.usage.input_tokens
    tokens_out = response.usage.output_tokens
    await token_budget.record_usage(user_id, tokens_in, tokens_out)

    # Inspect content blocks
    text_parts: list[str] = []
    escalation_reason: str | None = None
    for block in response.content:
        if block.type == "text":
            text_parts.append(block.text)
        elif block.type == "tool_use" and block.name == "escalate_to_human":
            input_dict = block.input if isinstance(block.input, dict) else {}
            escalation_reason = input_dict.get("reason", "no reason given")

    if escalation_reason is not None:
        log.info(
            "claude_requested_escalation",
            user_id=user_id,
            reason=escalation_reason,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
        )
        return ClaudeReply(
            text=None,
            escalation=True,
            escalation_reason=escalation_reason,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            model=chosen_model,
        )

    text = "\n".join(text_parts).strip()
    if not text:
        log.warning("claude_empty_response", user_id=user_id)
        return None

    log.info(
        "claude_replied",
        user_id=user_id,
        model=chosen_model,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        text_length=len(text),
    )
    return ClaudeReply(
        text=text,
        escalation=False,
        escalation_reason=None,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        model=chosen_model,
    )


def _build_messages(
    history: list[asyncpg.Record],
    latest_user_text: str,
) -> list[dict[str, Any]]:
    """Convert messages-table rows + latest user message into Anthropic API format.

    Format rules:
    - Conversation MUST start with role='user' (Anthropic API requirement)
    - Wrap user-side content in <user_message>...</user_message> for prompt-injection safety
    - Skip messages with NULL text (media-only)
    - The latest user message is appended explicitly (it may not yet be in `history`
      since the worker just inserted it before calling Claude)
    """
    api_messages: list[dict[str, Any]] = []

    for row in history:
        if not row["text"]:
            continue
        if row["direction"] == "in":
            api_messages.append({
                "role": "user",
                "content": f"<user_message>{row['text']}</user_message>",
            })
        else:
            # Outgoing — bot's previous reply
            api_messages.append({
                "role": "assistant",
                "content": row["text"],
            })

    # Append latest user message
    api_messages.append({
        "role": "user",
        "content": f"<user_message>{latest_user_text}</user_message>",
    })

    # Anthropic API requires alternation user/assistant.
    # If history starts with assistant (rare, e.g. welcome was sent before user said anything),
    # we drop leading assistants until we find first user.
    while api_messages and api_messages[0]["role"] != "user":
        api_messages.pop(0)

    return api_messages
```

### 4. Smart scenario handler

a) Создать `app/services/scenarios/smart.py`:

```python
"""Smart scenario — Claude-powered reply for messages without keyword matches.

Replaces echo as the engine fallback. Triggered by ScenarioEngine when:
- Conversation is active (not in handover)
- Event is event_type='message' (NOT comments — those go to comment-to-DM)
- No keyword matched
- User is not brand-new (new users get welcome instead)

Behavior:
- Calls claude_responder.respond() with conversation context
- If Claude returns text → wrap into OutgoingMessage and return
- If Claude requested escalation → set conversation status to handover_pending
  and return None (no reply sent — Yulia will handle)
- If Claude returned None (budget/error/empty) → return None (silent skip)

Note on safety:
This handler does NOT yet apply doTERRA banned-pattern filters to the response.
That layer is added in Task 14 — banned_patterns check between claude_responder
and the OutgoingMessage construction.
"""
from __future__ import annotations

import asyncpg

from app.models.events import IncomingEvent, OutgoingMessage
from app.repos import conversations as conversations_repo
from app.services import claude_responder
from app.services.scenario_engine import register_scenario
from app.utils.logging import get_logger

log = get_logger(__name__)


@register_scenario("smart")
async def handle_smart(
    event: IncomingEvent,
    user: asyncpg.Record,
    conversation: asyncpg.Record,
    scenario: asyncpg.Record,
) -> OutgoingMessage | None:
    if not event.text:
        log.info("smart_skipped_empty_text", user_id=user["id"])
        return None

    # Check user-level smart_mode flag (set by admin to disable AI for VIP/problematic accounts)
    if not user["smart_mode_enabled"]:
        log.info("smart_skipped_user_smart_disabled", user_id=user["id"])
        return None

    # Allow scenario row to override default model via metadata
    metadata = dict(scenario["metadata"]) if scenario["metadata"] else {}
    model = metadata.get("claude_model")  # None → use settings default

    reply = await claude_responder.respond(
        user_id=user["id"],
        conversation_id=conversation["id"],
        incoming_text=event.text,
        model=model,
    )

    if reply is None:
        # Budget exceeded / API error / empty — silently skip
        return None

    if reply.escalation:
        # Claude wants a human — flip status, do not send anything to user
        await conversations_repo.set_status(
            conversation["id"],
            "handover_pending",
            reason=f"claude: {reply.escalation_reason}",
        )
        log.info(
            "smart_escalated_to_human",
            user_id=user["id"],
            conv_id=conversation["id"],
            reason=reply.escalation_reason,
        )
        return None

    # Build outgoing message. Note: we don't set claude_used / claude_tokens here —
    # the worker (_send_and_record in tasks_messages.py) will write the row.
    # We need to extend that function to accept Claude metadata. See subtask 6.
    return OutgoingMessage(
        platform=event.platform,
        external_user_id=event.external_user_id,
        text=reply.text,
        scenario_id=scenario["id"],
    )
```

### 5. Регистрация smart в __init__

a) Обновить `app/services/scenarios/__init__.py`:

```python
"""Scenario implementations.

Each scenario lives in its own module. Importing this package
triggers handler registration via the @register_scenario decorator.
"""
from app.services.scenarios import comment_to_dm  # noqa: F401
from app.services.scenarios import echo  # noqa: F401  # kept for tests/back-compat
from app.services.scenarios import smart  # noqa: F401
from app.services.scenarios import welcome  # noqa: F401

__all__ = ["comment_to_dm", "echo", "smart", "welcome"]
```

### 6. Расширение worker — Claude metadata в messages

a) Текущий `_send_and_record` в `app/workers/tasks_messages.py` не сохраняет `claude_*` поля. Нужно протащить эти данные.

   Проблема: handler возвращает `OutgoingMessage`, в которой нет полей про Claude. Если расширить `OutgoingMessage` — это меняет ABC контракт.

   **Решение**: оставить `OutgoingMessage` нетронутой, а данные о Claude доставать из последнего вызова через прямой просмотр БД невозможно — поэтому ввожу опциональный параметр в `OutgoingMessage` без изменения семантики провайдеров. Провайдеры это поле игнорируют, worker использует.

   Ой, нет — это ломает `extra="forbid"` в Pydantic-модели и принцип чистоты ABC.

   **Лучшее решение:** smart handler возвращает обычный `OutgoingMessage`. Claude-метаданные (tokens, model) пишет в DB **сам smart handler**, через прямую запись в `messages` PRE-flight. Wait, но запись делается ПОСЛЕ provider.send.

   **Финальное решение, чистое:** добавить в smart handler логирование Claude usage в отдельную таблицу `claude_usage_log`, не трогая `messages.claude_*` поля в этой задаче. Колонки в `messages` остаются для будущего использования. В Task 14 (safety + admin) можно объединить.

   Но это усложнение. Я выбираю прагматичный вариант: **расширить `OutgoingMessage` опциональным полем `claude_metadata: dict | None`**. Поле опциональное, провайдеры его игнорируют, FakeProvider тоже. Worker если видит — пишет в `messages`.

b) Обновить `app/models/events.py` — добавить поле в OutgoingMessage:

```python
class OutgoingMessage(BaseModel):
    """A message to be sent via a MessagingProvider.

    Producers: ScenarioEngine.
    Consumers: MessagingProvider implementations (send).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    platform: Platform
    external_user_id: str = Field(min_length=1, max_length=255)
    text: str | None = None
    quick_replies: list[QuickReply] | None = None
    media_url: str | None = None
    reply_to_comment_id: str | None = None
    scenario_id: int | None = None
    claude_metadata: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Optional metadata attached when this message is generated by Claude. "
            "Keys: 'model', 'tokens_in', 'tokens_out'. Workers persist these "
            "into messages.claude_* columns. Providers ignore this field."
        ),
    )
```

   Не забыть импорт `Any` если ещё нет.

c) Обновить `app/services/scenarios/smart.py` — заполнять `claude_metadata`:

   Заменить блок построения OutgoingMessage в smart.py на:

```python
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

d) Обновить `app/workers/tasks_messages.py` — `_send_and_record` использует `claude_metadata` если есть:

   Найти блок в `_send_and_record`:

```python
    await messages.insert(
        conversation_id=conversation_id,
        direction="out",
        text=outgoing.text,
        media_url=outgoing.media_url,
        source="reply",
        scenario_id=outgoing.scenario_id,
        external_message_id=record_external_id,
    )
```

   Заменить на:

```python
    cm = outgoing.claude_metadata or {}
    await messages.insert(
        conversation_id=conversation_id,
        direction="out",
        text=outgoing.text,
        media_url=outgoing.media_url,
        source="reply",
        scenario_id=outgoing.scenario_id,
        claude_used=bool(cm),
        claude_model=cm.get("model"),
        claude_tokens_in=cm.get("tokens_in"),
        claude_tokens_out=cm.get("tokens_out"),
        external_message_id=record_external_id,
    )
```

### 7. Миграция: новый default_smart scenario

a) Создать `migrations/010_seed_smart_scenario.sql`:

```sql
-- Migration 010: Seed default smart scenario (Claude-powered fallback).
--
-- Replaces echo_scenario as the engine fallback. Echo remains in DB
-- for backward compatibility and explicit testing scenarios.

INSERT INTO scenarios (name, type, template, metadata, active)
VALUES (
    'default_smart',
    'smart',
    NULL,                                   -- smart doesn't use templates; Claude composes
    '{"claude_model": null}'::jsonb,        -- null → use settings.claude_default_model
    TRUE
)
ON CONFLICT (name) DO NOTHING;
```

### 8. Engine fallback переключение

a) В `app/services/scenario_engine.py` найти строку:

```python
    # 3. Fallback: echo scenario (testing only — replaced in Task 13 by smart/FAQ)
    if scenario_row is None:
        scenario_row = await scenarios_repo.get_by_name("echo_scenario")
```

   Заменить на:

```python
    # 3. Fallback: smart scenario (Claude-powered; was echo_scenario in early dev)
    if scenario_row is None:
        scenario_row = await scenarios_repo.get_by_name("default_smart")
```

   Echo остаётся в БД и в коде, но больше не fallback.

### 9. Тесты

a) Создать `tests/test_token_budget.py`:

```python
"""Tests for Claude token budget tracking."""
from __future__ import annotations

import pytest

from app.repos import token_budget
from app.repos.redis_client import get_redis


@pytest.fixture(autouse=True)
async def _clear_budget_keys() -> None:
    redis = await get_redis()
    keys = await redis.keys("claude:budget:*")
    if keys:
        await redis.delete(*keys)
    yield
    keys = await redis.keys("claude:budget:*")
    if keys:
        await redis.delete(*keys)


@pytest.mark.asyncio
async def test_can_call_claude_when_no_usage() -> None:
    assert await token_budget.can_call_claude(99001) is True


@pytest.mark.asyncio
async def test_record_usage_then_within_budget() -> None:
    await token_budget.record_usage(99002, tokens_in=1000, tokens_out=200)
    assert await token_budget.can_call_claude(99002) is True


@pytest.mark.asyncio
async def test_input_budget_exhausted() -> None:
    await token_budget.record_usage(
        99003,
        tokens_in=token_budget.INPUT_BUDGET_PER_DAY,
        tokens_out=0,
    )
    assert await token_budget.can_call_claude(99003) is False


@pytest.mark.asyncio
async def test_output_budget_exhausted() -> None:
    await token_budget.record_usage(
        99004,
        tokens_in=0,
        tokens_out=token_budget.OUTPUT_BUDGET_PER_DAY,
    )
    assert await token_budget.can_call_claude(99004) is False


@pytest.mark.asyncio
async def test_other_user_budget_independent() -> None:
    await token_budget.record_usage(
        99005,
        tokens_in=token_budget.INPUT_BUDGET_PER_DAY,
        tokens_out=0,
    )
    assert await token_budget.can_call_claude(99005) is False
    # Different user is unaffected
    assert await token_budget.can_call_claude(99006) is True
```

b) Создать `tests/test_claude_responder.py`:

```python
"""Tests for ClaudeResponder.

We mock the Anthropic SDK at the module-level _client to avoid real API calls.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.repos import conversations, messages, users
from app.services import claude_responder
from app.services.claude_responder import ClaudeReply, reset_client


@dataclass
class _FakeUsage:
    input_tokens: int
    output_tokens: int


@dataclass
class _FakeContentText:
    type: str = "text"
    text: str = ""


@dataclass
class _FakeContentToolUse:
    type: str = "tool_use"
    name: str = ""
    input: dict[str, Any] | None = None


@dataclass
class _FakeResponse:
    content: list[Any]
    usage: _FakeUsage


def _install_fake_client(monkeypatch: pytest.MonkeyPatch, response: _FakeResponse) -> MagicMock:
    """Replace _client in claude_responder with a mock that returns `response`."""
    fake_client = MagicMock()
    fake_messages = MagicMock()
    fake_messages.create = AsyncMock(return_value=response)
    fake_client.messages = fake_messages
    monkeypatch.setattr(claude_responder, "_client", fake_client)
    return fake_client


@pytest.fixture(autouse=True)
def _reset_responder_client() -> None:
    reset_client()
    yield
    reset_client()


async def _setup_user_and_conv(db, external_id: str = "claude_user_1"):
    user = await users.create(
        provider_name="sendpulse",
        platform="instagram",
        external_id=external_id,
    )
    conv = await conversations.create(user["id"], "instagram")
    return user, conv


@pytest.mark.asyncio
async def test_respond_returns_text_on_success(
    db, monkeypatch: pytest.MonkeyPatch,
) -> None:
    user, conv = await _setup_user_and_conv(db, "claude_ok")
    fake_response = _FakeResponse(
        content=[_FakeContentText(text="Конечно расскажу! 🌿")],
        usage=_FakeUsage(input_tokens=100, output_tokens=20),
    )
    _install_fake_client(monkeypatch, fake_response)

    reply = await claude_responder.respond(
        user_id=user["id"],
        conversation_id=conv["id"],
        incoming_text="Расскажи про очищение",
    )

    assert reply is not None
    assert reply.text == "Конечно расскажу! 🌿"
    assert reply.escalation is False
    assert reply.tokens_in == 100
    assert reply.tokens_out == 20


@pytest.mark.asyncio
async def test_respond_detects_escalation(
    db, monkeypatch: pytest.MonkeyPatch,
) -> None:
    user, conv = await _setup_user_and_conv(db, "claude_esc")
    fake_response = _FakeResponse(
        content=[
            _FakeContentToolUse(
                name="escalate_to_human",
                input={"reason": "Пользователь спрашивает про симптомы"},
            ),
        ],
        usage=_FakeUsage(input_tokens=80, output_tokens=15),
    )
    _install_fake_client(monkeypatch, fake_response)

    reply = await claude_responder.respond(
        user_id=user["id"],
        conversation_id=conv["id"],
        incoming_text="У меня болит голова, что использовать?",
    )

    assert reply is not None
    assert reply.escalation is True
    assert reply.escalation_reason is not None
    assert "симптом" in reply.escalation_reason.lower()
    assert reply.text is None


@pytest.mark.asyncio
async def test_respond_returns_none_on_empty_content(
    db, monkeypatch: pytest.MonkeyPatch,
) -> None:
    user, conv = await _setup_user_and_conv(db, "claude_empty")
    fake_response = _FakeResponse(
        content=[_FakeContentText(text="")],
        usage=_FakeUsage(input_tokens=10, output_tokens=0),
    )
    _install_fake_client(monkeypatch, fake_response)

    reply = await claude_responder.respond(
        user_id=user["id"],
        conversation_id=conv["id"],
        incoming_text="hi",
    )
    assert reply is None


@pytest.mark.asyncio
async def test_respond_returns_none_on_api_error(
    db, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from anthropic._exceptions import APIError

    user, conv = await _setup_user_and_conv(db, "claude_err")
    fake_client = MagicMock()
    fake_messages = MagicMock()
    fake_messages.create = AsyncMock(side_effect=APIError(
        message="boom", request=MagicMock(), body=None,
    ))
    fake_client.messages = fake_messages
    monkeypatch.setattr(claude_responder, "_client", fake_client)

    reply = await claude_responder.respond(
        user_id=user["id"],
        conversation_id=conv["id"],
        incoming_text="hi",
    )
    assert reply is None


@pytest.mark.asyncio
async def test_respond_blocks_on_budget_exceeded(
    db, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.repos import token_budget

    user, conv = await _setup_user_and_conv(db, "claude_budget")
    # Saturate budget
    await token_budget.record_usage(
        user["id"],
        tokens_in=token_budget.INPUT_BUDGET_PER_DAY,
        tokens_out=0,
    )

    # Even though API would succeed, we should not call it
    create_mock = AsyncMock()
    fake_client = MagicMock()
    fake_messages = MagicMock()
    fake_messages.create = create_mock
    fake_client.messages = fake_messages
    monkeypatch.setattr(claude_responder, "_client", fake_client)

    reply = await claude_responder.respond(
        user_id=user["id"],
        conversation_id=conv["id"],
        incoming_text="hi",
    )

    assert reply is None
    create_mock.assert_not_called()


@pytest.mark.asyncio
async def test_build_messages_wraps_user_in_xml(db) -> None:
    user, conv = await _setup_user_and_conv(db, "claude_xml")
    # Insert one previous turn
    await messages.insert(
        conversation_id=conv["id"],
        direction="in",
        text="Hi there",
        external_message_id="hist_1",
    )
    await messages.insert(
        conversation_id=conv["id"],
        direction="out",
        text="Hello!",
        external_message_id="hist_2",
    )
    history = await messages.get_recent(conv["id"], limit=20)

    api_msgs = claude_responder._build_messages(history, latest_user_text="What about oils?")

    # Last message is the new user message, wrapped
    assert api_msgs[-1]["role"] == "user"
    assert "<user_message>What about oils?</user_message>" in api_msgs[-1]["content"]

    # Earlier user message also wrapped
    user_msgs = [m for m in api_msgs if m["role"] == "user"]
    assert all("<user_message>" in m["content"] for m in user_msgs)

    # Assistant message NOT wrapped
    asst_msgs = [m for m in api_msgs if m["role"] == "assistant"]
    assert all("<user_message>" not in m["content"] for m in asst_msgs)


@pytest.mark.asyncio
async def test_records_token_usage_after_call(
    db, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.repos import token_budget
    from app.repos.redis_client import get_redis

    user, conv = await _setup_user_and_conv(db, "claude_usage")
    redis = await get_redis()
    await redis.delete(token_budget._input_key(user["id"]))
    await redis.delete(token_budget._output_key(user["id"]))

    fake_response = _FakeResponse(
        content=[_FakeContentText(text="answer")],
        usage=_FakeUsage(input_tokens=250, output_tokens=80),
    )
    _install_fake_client(monkeypatch, fake_response)

    await claude_responder.respond(
        user_id=user["id"],
        conversation_id=conv["id"],
        incoming_text="q",
    )

    in_used = int(await redis.get(token_budget._input_key(user["id"])) or 0)
    out_used = int(await redis.get(token_budget._output_key(user["id"])) or 0)
    assert in_used == 250
    assert out_used == 80
```

c) Создать `tests/test_scenario_smart.py`:

```python
"""Tests for smart scenario handler."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from app.models.events import IncomingEvent
from app.repos import conversations, scenarios as scenarios_repo, users
from app.services import claude_responder
from app.services.claude_responder import ClaudeReply
from app.services.scenarios.smart import handle_smart


def _event(text: str = "А что входит в программу?") -> IncomingEvent:
    return IncomingEvent(
        provider="sendpulse",
        platform="instagram",
        event_type="message",
        external_user_id="smart_user_e",
        external_event_id="evt_s_1",
        text=text,
        occurred_at=datetime(2026, 5, 8, 12, 0, tzinfo=UTC),
    )


async def _setup(db, external_id: str = "smart_user"):
    user = await users.create(
        provider_name="sendpulse",
        platform="instagram",
        external_id=external_id,
    )
    conv = await conversations.create(user["id"], "instagram")
    scenario = await scenarios_repo.get_by_name("default_smart")
    assert scenario is not None, "default_smart must be seeded by migration 010"
    return user, conv, scenario


@pytest.mark.asyncio
async def test_smart_returns_outgoing_message(db, monkeypatch: pytest.MonkeyPatch) -> None:
    user, conv, scenario = await _setup(db, "smart_ok")
    monkeypatch.setattr(
        claude_responder,
        "respond",
        AsyncMock(return_value=ClaudeReply(
            text="Программа включает 30 дней с маслами 🌿",
            escalation=False,
            escalation_reason=None,
            tokens_in=120,
            tokens_out=30,
            model="claude-sonnet-4-6",
        )),
    )

    msg = await handle_smart(_event(), user, conv, scenario)

    assert msg is not None
    assert "30 дней" in msg.text
    assert msg.scenario_id == scenario["id"]
    assert msg.claude_metadata == {
        "model": "claude-sonnet-4-6",
        "tokens_in": 120,
        "tokens_out": 30,
    }


@pytest.mark.asyncio
async def test_smart_escalation_sets_handover(db, monkeypatch: pytest.MonkeyPatch) -> None:
    user, conv, scenario = await _setup(db, "smart_esc")
    monkeypatch.setattr(
        claude_responder,
        "respond",
        AsyncMock(return_value=ClaudeReply(
            text=None,
            escalation=True,
            escalation_reason="Симптомы — нужен врач",
            tokens_in=80,
            tokens_out=15,
            model="claude-sonnet-4-6",
        )),
    )

    msg = await handle_smart(_event(text="у меня болит голова"), user, conv, scenario)

    assert msg is None  # no reply sent

    updated = await db.fetchrow("SELECT * FROM conversations WHERE id = $1", conv["id"])
    assert updated["status"] == "handover_pending"
    assert "Симптомы" in (updated["handover_reason"] or "")


@pytest.mark.asyncio
async def test_smart_skipped_when_smart_mode_disabled(
    db, monkeypatch: pytest.MonkeyPatch,
) -> None:
    user, conv, scenario = await _setup(db, "smart_disabled")
    await db.execute(
        "UPDATE social_users SET smart_mode_enabled = FALSE WHERE id = $1",
        user["id"],
    )
    user_disabled = await db.fetchrow(
        "SELECT * FROM social_users WHERE id = $1", user["id"],
    )

    respond_mock = AsyncMock()
    monkeypatch.setattr(claude_responder, "respond", respond_mock)

    msg = await handle_smart(_event(), user_disabled, conv, scenario)
    assert msg is None
    respond_mock.assert_not_called()


@pytest.mark.asyncio
async def test_smart_skipped_on_empty_text(db, monkeypatch: pytest.MonkeyPatch) -> None:
    user, conv, scenario = await _setup(db, "smart_empty")
    respond_mock = AsyncMock()
    monkeypatch.setattr(claude_responder, "respond", respond_mock)

    event_no_text = _event(text="")
    msg = await handle_smart(event_no_text, user, conv, scenario)

    assert msg is None
    respond_mock.assert_not_called()


@pytest.mark.asyncio
async def test_smart_returns_none_when_responder_returns_none(
    db, monkeypatch: pytest.MonkeyPatch,
) -> None:
    user, conv, scenario = await _setup(db, "smart_none")
    monkeypatch.setattr(
        claude_responder,
        "respond",
        AsyncMock(return_value=None),
    )

    msg = await handle_smart(_event(), user, conv, scenario)
    assert msg is None
```

d) Создать `tests/test_e2e_smart_pipeline.py`:

```python
"""E2E: returning user without keyword match → smart scenario fallback → Claude reply."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import AsyncClient

from app.models.events import IncomingEvent
from app.repos import events as events_repo, users
from app.repos.redis_client import get_redis
from app.services import claude_responder, lead_tracker
from app.workers.tasks_messages import process_incoming_event
from tests.fakes.fake_provider import FakeProvider


@dataclass
class _FakeUsage:
    input_tokens: int
    output_tokens: int


@dataclass
class _FakeContentText:
    type: str = "text"
    text: str = ""


@dataclass
class _FakeResponse:
    content: list
    usage: _FakeUsage


@pytest.mark.asyncio
async def test_returning_user_smart_reply(
    client: AsyncClient,
    fake_provider: FakeProvider,
    db,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Pre-create user and mark welcome as sent
    user = await users.create(
        provider_name="sendpulse",
        platform="instagram",
        external_id="e2e_smart_user",
        full_name="Anna",
    )
    await lead_tracker.mark_welcome_sent(user["id"])

    # Mock Anthropic
    fake_response = _FakeResponse(
        content=[_FakeContentText(text="В программе 30 дней с эфирными маслами 🌿")],
        usage=_FakeUsage(input_tokens=200, output_tokens=50),
    )
    fake_client = MagicMock()
    fake_messages = MagicMock()
    fake_messages.create = AsyncMock(return_value=fake_response)
    fake_client.messages = fake_messages
    monkeypatch.setattr(claude_responder, "_client", fake_client)

    # Reset budget
    redis = await get_redis()
    from app.repos import token_budget
    await redis.delete(token_budget._input_key(user["id"]))
    await redis.delete(token_budget._output_key(user["id"]))

    # Send DM with no matching keyword (so engine falls back to smart)
    event = IncomingEvent(
        provider="sendpulse",
        platform="instagram",
        event_type="message",
        external_user_id="e2e_smart_user",
        external_event_id="e2e_smart_evt",
        username="anna_p",
        text="А что входит в программу?",
        occurred_at=datetime(2026, 5, 8, 12, 0, tzinfo=UTC),
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

    # Anthropic was called once
    fake_messages.create.assert_called_once()

    # Outgoing message recorded with claude metadata
    msg = await db.fetchrow(
        """
        SELECT m.* FROM messages m
        JOIN conversations c ON c.id = m.conversation_id
        WHERE c.user_id = $1 AND m.direction = 'out'
        ORDER BY m.created_at DESC
        LIMIT 1
        """,
        user["id"],
    )
    assert msg is not None
    assert "30 дней" in msg["text"]
    assert msg["claude_used"] is True
    assert msg["claude_model"] == "claude-sonnet-4-6"
    assert msg["claude_tokens_in"] == 200
    assert msg["claude_tokens_out"] == 50

    # FakeProvider received the message
    assert len(fake_provider.sent) == 1
    assert "30 дней" in fake_provider.sent[0].text
```

---

## Acceptance criteria

- [ ] Файлы созданы по структуре подзадач 1–8
- [ ] Миграция 010 применена: `SELECT name, type FROM scenarios WHERE name='default_smart'` возвращает строку с type='smart'
- [ ] `make lint` проходит
- [ ] `make test` проходит, все новые тесты зелёные:
  - `test_token_budget.py` — 5 тестов
  - `test_claude_responder.py` — 7 тестов
  - `test_scenario_smart.py` — 5 тестов
  - `test_e2e_smart_pipeline.py` — 1 ключевой тест
  - Все существующие тесты Tasks 01, 03, 04, 06, 07, 08, 09, 11 продолжают работать
- [ ] System prompt загружается при первом обращении и кэшируется (повторное чтение `_load_system_prompt()` не открывает файл — можно проверить через temporary monkeypatch на `Path.read_text`)
- [ ] При наличии `ANTHROPIC_API_KEY` в `.env` ручная проверка через docker:
  ```bash
  # POST DM с обычным вопросом → проверить, что в логах видно claude_replied
  # и в messages есть строка с claude_used=true
  ```
- [ ] При намеренном symptoms-запросе («у меня болит голова») в e2e-тесте видно `smart_escalated_to_human` и conversation.status='handover_pending'

---

## Do NOT

- НЕ применять safety-фильтры на ответ Claude в этой задаче. Это Task 14.
- НЕ давать Claude инструменты для отправки сообщений напрямую. Только `escalate_to_human`. Если нужен ещё какой-то tool — отдельная задача.
- НЕ читать system prompt из БД. Файл — git-managed, БД — лишний слой.
- НЕ кэшировать prompt в Redis. Локальная переменная процесса достаточна.
- НЕ делать retry на API ошибки внутри `claude_responder`. arq делает retry на уровне worker (Task 06). Двойной retry приведёт к лишним токенам.
- НЕ передавать `media_url` в Claude в этом таске. Только текст. Vision-mode — отдельная задача в будущем.
- НЕ удалять echo сценарий из БД. Он остаётся для тестов и совместимости.
- НЕ делать default_smart реактивным к keyword'ам. Smart — это fallback, а не keyword scenario.
- НЕ ставить max_tokens > 1000. Bot должен писать кратко (как сказано в system prompt).
- НЕ использовать stream=True. Worker записывает messages один раз, в конце; streaming усложняет idempotency.

---

## Зависимости задачи

- Tasks 01, 03, 04, 06, 07, 08, 09, 11 применены
- В `.env` есть `ANTHROPIC_API_KEY` (для прода). Для тестов не требуется — Anthropic SDK замокан.
- Не требует Task 05 (SendPulseProvider) — тесты идут через FakeProvider
- Не требует Task 14 (safety) — safety фильтры добавятся слоем поверх

---

## Что после этой задачи

После применения у тебя есть **полноценная воронка с осмысленным conversational layer**:

```
✅ Acquisition (08, 09): welcome / comment-to-DM с deep-link
✅ Conversation (13): Claude отвечает на вопросы после welcome
✅ Escalation (13): пользователь со сложным вопросом → handover_pending
✅ Lead handover (11): bot_purify забирает контекст и фиксирует переход
✅ Budget control (13): per-user-day защита от абуза
```

Дальше:

- **Task 14** — Safety filters: regex-проверки на medical claims в исходящих + полная handover-логика для команды «оператор»
- **Task 05** — SendPulseProvider: реальная отправка
- **Task 15** — Admin dashboard
- **Task 16-18** — мониторинг, deploy, go-live

После Tasks 14 и 05 проект готов к запуску.

---

**Дата создания:** 2026-05-08
**Применять в:** `D:\Work\social_inbox` после Tasks 01, 03, 04, 06, 07, 08, 09, 11
**Эстимейт:** 5–6 часов на Claude Code + ручная проверка
