# Task 09: Comment-to-DM scenario

> Применить в `D:\Work\social_inbox` после Tasks 01, 03, 04, 06, 07, 08. Положить файл в корень проекта, открыть в VS Code, попросить Claude Code: «Прочитай TASK_09_comment_to_dm.md и выполни все подзадачи последовательно. Перед каждой подзадачей кратко опиши план, после — что фактически сделано».

---

## Контекст

После Task 08 у нас работает welcome-сценарий: пользователь пишет в DM → бот отвечает с deep-link в Telegram. Это работает.

Но **главный acquisition-канал** Юли — комментарии под Reels. Подписчик пишет «ОЧИЩЕНИЕ» под видео → бот ловит это → отправляет private reply в DM с приглашением в Telegram. Это и есть Comment-to-DM.

**Принципиальные отличия от welcome:**

| Параметр | Welcome | Comment-to-DM |
|----------|---------|---------------|
| Триггер | первый DM | комментарий с ключевым словом |
| `OutgoingMessage.reply_to_comment_id` | None | `comment_id` |
| API на стороне провайдера | обычный send | private reply |
| Idempotency-ключ | per-user lifetime | per-(user, post) |
| Видимость поста другим | — | можно ответить и на сам комментарий |

В Task 09 делаем именно эту специфику. Сам шаблон сообщения структурно похож на welcome (deep-link, quick replies, disclaimer), но текст другой.

---

## Цель

После выполнения этой задачи:

- Создан `app/services/scenarios/comment_to_dm.py` с handler'ом, зарегистрированным как тип `'comment_to_dm'`
- В БД через миграцию посеян сценарий `default_purify_comment` со шаблоном текста и привязкой к keyword «ОЧИЩЕНИЕ»
- ScenarioEngine: для events `event_type='comment'` сначала смотрит в `comment_triggers` (post-specific), потом в `keywords` (глобальные). Это требует небольшого расширения engine.
- Per-(user, post) idempotency реализована — повторный комментарий с тем же keyword под тем же постом не вызывает повторного DM
- `OutgoingMessage.reply_to_comment_id` корректно заполняется
- Опциональный «public reply» к самому комментарию (типа «Отправила в личку! 💌») — за флагом в metadata сценария
- E2E-тесты покрывают: keyword match по комментарию, post-specific override, idempotency по (user, post), отправка с заполненным `reply_to_comment_id`
- `make test` зелёный

---

## Подзадачи

### 1. Миграция 008 — расширение dedup-таблицы

a) Создать `migrations/008_comment_user_dedup.sql`:

```sql
-- Migration 008: per-(user, post) idempotency for comment-to-DM.
--
-- Existing comment_replies_dedup (migration 004) is keyed by comment_id alone,
-- which protects against the same comment being processed twice (rare —
-- arq + events_log unique idx already help). The harder problem is:
-- one user posts MULTIPLE comments under the same Reels (e.g. spamming
-- "ОЧИЩЕНИЕ ОЧИЩЕНИЕ ОЧИЩЕНИЕ" in 3 separate comments). We want
-- exactly ONE DM, not three.
--
-- Add a separate composite uniqueness on (social_user_id, platform, post_id, scenario_id)
-- via a new table. Reusing comment_replies_dedup wouldn't fit semantically.

CREATE TABLE IF NOT EXISTS comment_user_dedup (
    id              BIGSERIAL PRIMARY KEY,
    user_id         BIGINT NOT NULL REFERENCES social_users(id) ON DELETE CASCADE,
    platform        TEXT NOT NULL CHECK (platform IN ('instagram', 'facebook')),
    post_id         TEXT NOT NULL,
    scenario_id     BIGINT NOT NULL REFERENCES scenarios(id) ON DELETE CASCADE,
    replied_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (user_id, platform, post_id, scenario_id)
);

CREATE INDEX IF NOT EXISTS idx_comment_user_dedup_user
    ON comment_user_dedup(user_id);
```

### 2. Миграция 009 — сид default Comment-to-DM сценария

a) Создать `migrations/009_seed_comment_scenarios.sql`:

```sql
-- Migration 009: Seed default comment-to-DM scenario + global keyword for "ОЧИЩЕНИЕ".

-- Scenario for comment-to-DM with default 'purify' slug.
INSERT INTO scenarios (name, type, template, metadata, active)
VALUES (
    'default_purify_comment',
    'comment_to_dm',
    E'🌿 Привет, {first_name}! Спасибо за интерес к программе «Очищение» 💚\n\nСейчас расскажу подробнее в Telegram — там удобнее и быстрее:\n\n👉 {tg_link}\n\n{disclaimer}',
    '{"tg_scenario_slug": "purify", "public_reply_text": "Отправила в личку 💌", "quick_replies": [{"title": "Перейти в Telegram", "type": "url", "payload": "{tg_link}"}]}'::jsonb,
    TRUE
)
ON CONFLICT (name) DO NOTHING;

-- Global keyword: "ОЧИЩЕНИЕ" anywhere in a comment → trigger this scenario.
INSERT INTO keywords (keyword, match_type, context, scenario_id, priority, case_sensitive, active)
SELECT 'очищение', 'contains', 'comment', s.id, 50, FALSE, TRUE
FROM scenarios s
WHERE s.name = 'default_purify_comment'
ON CONFLICT DO NOTHING;
```

   **Замечание:** В keyword кладём lowercase `очищение` потому что match_type=contains + case_sensitive=FALSE. Юля может сказать подписчикам писать любое из «ОЧИЩЕНИЕ», «очищение», «Очищение» — все три попадут.

### 3. Репозиторий comment_triggers + comment_user_dedup

a) Создать `app/repos/comment_triggers.py`:

```python
"""Repository for comment_triggers + comment_user_dedup tables.

comment_triggers — post-specific overrides for keyword routing.
    Use case: under Reels A, "ОЧИЩЕНИЕ" → scenario 'purify',
              under Reels B, "ОЧИЩЕНИЕ" → scenario 'oils'.
    Falls back to global keywords table if no row matches.

comment_user_dedup — per-(user, post, scenario) idempotency.
    Ensures we send exactly ONE DM even if the user posts 5 comments.
"""
from __future__ import annotations

import asyncpg

from app.repos.pool import get_pool


# ---- comment_triggers ----

async def find_for_post(
    platform: str,
    post_id: str,
    text: str,
) -> asyncpg.Record | None:
    """Find a post-specific trigger matching the comment text.

    Match logic: case-insensitive 'contains'. Simple by design — comment_triggers
    is a small admin-managed table, not a flexible keyword DSL.
    """
    pool = await get_pool()
    return await pool.fetchrow(
        """
        SELECT * FROM comment_triggers
        WHERE platform = $1
          AND post_id = $2
          AND active = TRUE
          AND $3 ILIKE '%' || keyword || '%'
        LIMIT 1
        """,
        platform, post_id, text,
    )


# ---- comment_user_dedup ----

async def already_replied(
    *,
    user_id: int,
    platform: str,
    post_id: str,
    scenario_id: int,
) -> bool:
    """Return True if we already replied to this user under this post for this scenario."""
    pool = await get_pool()
    row = await pool.fetchrow(
        """
        SELECT 1 FROM comment_user_dedup
        WHERE user_id = $1
          AND platform = $2
          AND post_id = $3
          AND scenario_id = $4
        """,
        user_id, platform, post_id, scenario_id,
    )
    return row is not None


async def mark_replied(
    *,
    user_id: int,
    platform: str,
    post_id: str,
    scenario_id: int,
) -> None:
    """Record that we sent a DM in response to this user's comment under this post."""
    pool = await get_pool()
    try:
        await pool.execute(
            """
            INSERT INTO comment_user_dedup (user_id, platform, post_id, scenario_id)
            VALUES ($1, $2, $3, $4)
            """,
            user_id, platform, post_id, scenario_id,
        )
    except asyncpg.UniqueViolationError:
        # Concurrent insert — already deduped by another worker. Safe to ignore.
        pass
```

### 4. Расширение ScenarioEngine — comment_triggers lookup

a) Обновить `app/services/scenario_engine.py` — функцию `handle()`. Найти блок:

```python
    # 1. Try keyword match
    km: KeywordMatch | None = None
    if event.text:
        km = await match_keywords(event.text, context)

    scenario_row: asyncpg.Record | None = None

    if km:
        scenario_row = await scenarios_repo.get_by_id(km.scenario_id)
```

   Заменить на:

```python
    scenario_row: asyncpg.Record | None = None

    # 1. For comments: check post-specific triggers FIRST.
    # Allows per-Reels overrides like "ОЧИЩЕНИЕ" → 'oils' scenario under specific posts.
    if event.event_type == "comment" and event.post_id and event.text:
        from app.repos import comment_triggers as ct_repo
        trigger = await ct_repo.find_for_post(event.platform, event.post_id, event.text)
        if trigger:
            scenario_row = await scenarios_repo.get_by_id(trigger["scenario_id"])
            if scenario_row is None:
                log.warning(
                    "comment_trigger_scenario_missing",
                    trigger_id=trigger["id"],
                    scenario_id=trigger["scenario_id"],
                )

    # 2. Fall back to global keywords match
    if scenario_row is None and event.text:
        km = await match_keywords(event.text, context)
        if km:
            scenario_row = await scenarios_repo.get_by_id(km.scenario_id)
            if scenario_row is None:
                log.warning(
                    "keyword_matched_but_scenario_missing",
                    keyword_id=km.keyword_id,
                    scenario_id=km.scenario_id,
                )
```

b) Импорт `from app.repos import comment_triggers as ct_repo` сделать на уровне функции, чтобы не вводить циклические зависимости (engine → repos → ничего → ок). Можно поднять на верх файла, если предпочтительно — компилятор не возражает.

### 5. Comment-to-DM scenario handler

a) Создать `app/services/scenarios/comment_to_dm.py`:

```python
"""Comment-to-DM scenario.

Triggered by ScenarioEngine when a user posts a comment with a matching keyword
under one of Yulia's Reels/posts. Sends a private reply (DM) tied to that comment,
with a Telegram deep-link, and optionally a public reply on the comment itself.

Idempotency:
- Per-(user, post, scenario) — exactly one DM even if user posts 5 comments
- Implemented via comment_user_dedup table (migration 008)

Re-uses welcome's lifetime flag in lead_tracker.was_welcome_sent / mark_welcome_sent.
Reasoning: welcome and comment-to-DM are both "first-touch lead magnet". If the
user got a welcome-via-DM yesterday and posts a comment today, we don't want
another deep-link DM — they already have the link. The comment is acknowledged
through the optional public reply.
"""
from __future__ import annotations

from typing import Any

import asyncpg

from app.models.events import IncomingEvent, OutgoingMessage, QuickReply
from app.repos import comment_triggers as ct_repo
from app.services import lead_tracker
from app.services.scenario_engine import register_scenario
from app.services.scenarios.welcome import DEFAULT_FIRST_NAME, DISCLAIMER, _extract_first_name
from app.utils.logging import get_logger

log = get_logger(__name__)


@register_scenario("comment_to_dm")
async def handle_comment_to_dm(
    event: IncomingEvent,
    user: asyncpg.Record,
    conversation: asyncpg.Record,
    scenario: asyncpg.Record,
) -> OutgoingMessage | None:
    # Sanity: this scenario only fires on comments
    if event.event_type != "comment":
        log.warning(
            "comment_to_dm_called_for_non_comment",
            event_type=event.event_type,
            user_id=user["id"],
        )
        return None

    if not event.comment_id or not event.post_id:
        log.warning(
            "comment_to_dm_missing_ids",
            user_id=user["id"],
            comment_id=event.comment_id,
            post_id=event.post_id,
        )
        return None

    # 1. Per-(user, post, scenario) idempotency
    if await ct_repo.already_replied(
        user_id=user["id"],
        platform=event.platform,
        post_id=event.post_id,
        scenario_id=scenario["id"],
    ):
        log.info(
            "comment_to_dm_skipped_already_replied",
            user_id=user["id"],
            post_id=event.post_id,
            scenario_id=scenario["id"],
        )
        return None

    # 2. Lifetime welcome-flag (re-use, see module docstring rationale)
    if await lead_tracker.was_welcome_sent(user["id"]):
        # Mark deduped anyway, to prevent the engine from retrying this comment
        await ct_repo.mark_replied(
            user_id=user["id"],
            platform=event.platform,
            post_id=event.post_id,
            scenario_id=scenario["id"],
        )
        log.info(
            "comment_to_dm_skipped_user_already_received_welcome",
            user_id=user["id"],
        )
        return None

    # 3. Resolve scenario_slug
    metadata = dict(scenario["metadata"]) if scenario["metadata"] else {}
    scenario_slug = metadata.get("tg_scenario_slug", "purify")

    # 4. Build deep-link
    try:
        tg_link = lead_tracker.build_deep_link(user["short_id"], scenario_slug)
    except ValueError as exc:
        log.error(
            "comment_to_dm_invalid_slug",
            scenario_id=scenario["id"],
            slug=scenario_slug,
            error=str(exc),
        )
        return None

    # 5. Resolve template
    first_name = _extract_first_name(user["full_name"]) or DEFAULT_FIRST_NAME
    template = scenario["template"] or ""
    text = template.format(
        first_name=first_name,
        tg_link=tg_link,
        disclaimer=DISCLAIMER,
    )

    # 6. Quick replies
    quick_replies = _build_quick_replies(metadata, tg_link)

    # 7. Mark deduped + welcome-sent BEFORE returning, same logic as welcome.py
    await ct_repo.mark_replied(
        user_id=user["id"],
        platform=event.platform,
        post_id=event.post_id,
        scenario_id=scenario["id"],
    )
    await lead_tracker.mark_welcome_sent(user["id"])

    log.info(
        "comment_to_dm_built",
        user_id=user["id"],
        short_id=user["short_id"],
        post_id=event.post_id,
        comment_id=event.comment_id,
        slug=scenario_slug,
    )

    return OutgoingMessage(
        platform=event.platform,
        external_user_id=event.external_user_id,
        text=text,
        quick_replies=quick_replies,
        reply_to_comment_id=event.comment_id,  # ← KEY DIFFERENCE from welcome
        scenario_id=scenario["id"],
    )


def _build_quick_replies(
    metadata: dict[str, Any],
    tg_link: str,
) -> list[QuickReply] | None:
    """Same shape as welcome._build_quick_replies but kept local to this module
    to allow independent evolution of comment-to-DM UX.
    """
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
        resolved_payload = payload.replace("{tg_link}", tg_link)
        try:
            out.append(QuickReply(title=title, payload=resolved_payload))
        except Exception as exc:
            log.warning("quick_reply_invalid", error=str(exc))
            continue
    return out or None
```

### 6. Регистрация в __init__

a) Обновить `app/services/scenarios/__init__.py`:

```python
"""Scenario implementations.

Each scenario lives in its own module. Importing this package
triggers handler registration via the @register_scenario decorator.
"""
from app.services.scenarios import comment_to_dm  # noqa: F401 — side effect only
from app.services.scenarios import echo  # noqa: F401 — side effect only
from app.services.scenarios import welcome  # noqa: F401 — side effect only

__all__ = ["comment_to_dm", "echo", "welcome"]
```

### 7. Public reply поддержка (опционально на этом этапе)

a) Optional public reply на сам комментарий — это **отдельный API call** через провайдера (новый метод `reply_to_comment` в `MessagingProvider`). На этом этапе мы не добавляем метод в ABC, потому что:
- Это меняет интерфейс провайдера → влияет на FakeProvider, SendPulseProvider скелет, manychat/meta заглушки
- Реальная отправка public reply живёт в SendPulseProvider (Task 05)

Решение: оставить логику чтения `metadata.public_reply_text` в handler'е через **TODO-комментарий** для Task 05. Сам текст и флаг сохраняются в БД, но фактически не используются на этапе Task 09.

b) В `app/services/scenarios/comment_to_dm.py` добавить в конец функции (перед return):

```python
    # TODO(Task 05): if metadata.get('public_reply_text'), call
    # provider.reply_to_comment(event.comment_id, metadata['public_reply_text']).
    # Requires extending MessagingProvider ABC with reply_to_comment method
    # and implementing it in SendPulseProvider.
    public_reply = metadata.get("public_reply_text")
    if public_reply:
        log.info(
            "public_reply_pending_task_05",
            comment_id=event.comment_id,
            text=public_reply,
        )
```

### 8. Worker не меняем

a) `app/workers/tasks_messages.py` уже корректно вызывает `scenario_engine.handle()` для всех типов событий. Для `event_type='comment'` worker:
- Создаёт/находит пользователя (как для DM)
- Создаёт/находит conversation (один на пользователя+платформу — это ОК, comments объединяются с DMs в один диалог)
- Записывает входящее сообщение с `source='comment'` (это уже делается в Task 06: `_source_from_event_type('comment') = 'comment'`)
- Вызывает engine
- Engine возвращает `OutgoingMessage` с `reply_to_comment_id` → worker отправляет через provider.send

Никаких изменений не требуется.

### 9. Тесты

a) Создать `tests/test_repos_comment_triggers.py`:

```python
"""Tests for comment_triggers + comment_user_dedup repos."""
from __future__ import annotations

import pytest

from app.repos import comment_triggers as ct_repo
from app.repos import scenarios as scenarios_repo, users


async def _seed_scenario(db, name: str = "ct_test_s") -> int:
    row = await db.fetchrow(
        """
        INSERT INTO scenarios (name, type, active) VALUES ($1, 'comment_to_dm', TRUE)
        RETURNING id
        """,
        name,
    )
    return row["id"]


@pytest.mark.asyncio
async def test_find_for_post_returns_match(db) -> None:
    sid = await _seed_scenario(db, "ct_find_1")
    await db.execute(
        """
        INSERT INTO comment_triggers (platform, post_id, keyword, scenario_id, active)
        VALUES ('instagram', 'post_42', 'ОЧИЩЕНИЕ', $1, TRUE)
        """,
        sid,
    )

    result = await ct_repo.find_for_post("instagram", "post_42", "хочу ОЧИЩЕНИЕ программу")
    assert result is not None
    assert result["scenario_id"] == sid


@pytest.mark.asyncio
async def test_find_for_post_case_insensitive(db) -> None:
    sid = await _seed_scenario(db, "ct_find_ci")
    await db.execute(
        """
        INSERT INTO comment_triggers (platform, post_id, keyword, scenario_id, active)
        VALUES ('instagram', 'post_ci', 'oils', $1, TRUE)
        """,
        sid,
    )

    result = await ct_repo.find_for_post("instagram", "post_ci", "Хочу OILS!")
    assert result is not None


@pytest.mark.asyncio
async def test_find_for_post_no_match_returns_none(db) -> None:
    result = await ct_repo.find_for_post("instagram", "nonexistent_post", "anything")
    assert result is None


@pytest.mark.asyncio
async def test_dedup_already_replied_lifecycle(db) -> None:
    sid = await _seed_scenario(db, "ct_dedup_1")
    user = await users.create(
        provider_name="sendpulse", platform="instagram", external_id="ct_dedup_user_1",
    )

    # Initially: no record
    assert await ct_repo.already_replied(
        user_id=user["id"], platform="instagram", post_id="p1", scenario_id=sid,
    ) is False

    # Mark replied
    await ct_repo.mark_replied(
        user_id=user["id"], platform="instagram", post_id="p1", scenario_id=sid,
    )

    # Now: True
    assert await ct_repo.already_replied(
        user_id=user["id"], platform="instagram", post_id="p1", scenario_id=sid,
    ) is True


@pytest.mark.asyncio
async def test_dedup_different_post_not_blocked(db) -> None:
    sid = await _seed_scenario(db, "ct_dedup_2")
    user = await users.create(
        provider_name="sendpulse", platform="instagram", external_id="ct_dedup_user_2",
    )
    await ct_repo.mark_replied(
        user_id=user["id"], platform="instagram", post_id="p1", scenario_id=sid,
    )

    # Different post — independent
    assert await ct_repo.already_replied(
        user_id=user["id"], platform="instagram", post_id="p2", scenario_id=sid,
    ) is False


@pytest.mark.asyncio
async def test_dedup_mark_twice_no_error(db) -> None:
    """mark_replied should be idempotent — UniqueViolation handled silently."""
    sid = await _seed_scenario(db, "ct_dedup_3")
    user = await users.create(
        provider_name="sendpulse", platform="instagram", external_id="ct_dedup_user_3",
    )
    await ct_repo.mark_replied(
        user_id=user["id"], platform="instagram", post_id="p1", scenario_id=sid,
    )
    # Second call — should not raise
    await ct_repo.mark_replied(
        user_id=user["id"], platform="instagram", post_id="p1", scenario_id=sid,
    )
```

b) Создать `tests/test_scenario_comment_to_dm.py`:

```python
"""Tests for comment_to_dm scenario handler."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.models.events import IncomingEvent
from app.repos import (
    comment_triggers as ct_repo,
    conversations,
    scenarios as scenarios_repo,
    users,
)
from app.repos.redis_client import get_redis
from app.services import lead_tracker
from app.services.scenarios.comment_to_dm import handle_comment_to_dm


def _make_comment_event(
    text: str = "ОЧИЩЕНИЕ хочу!",
    external_user_id: str = "comment_user_1",
    post_id: str = "post_1",
    comment_id: str = "comment_1",
) -> IncomingEvent:
    return IncomingEvent(
        provider="sendpulse",
        platform="instagram",
        event_type="comment",
        external_user_id=external_user_id,
        external_event_id=f"evt_c_{comment_id}",
        full_name="Маша Петрова",
        text=text,
        post_id=post_id,
        comment_id=comment_id,
        occurred_at=datetime(2026, 4, 30, 12, 0, tzinfo=UTC),
    )


async def _setup(db, external_id: str):
    user = await users.create(
        provider_name="sendpulse",
        platform="instagram",
        external_id=external_id,
        full_name="Маша Петрова",
    )
    conv = await conversations.create(user["id"], "instagram")
    scenario = await scenarios_repo.get_by_name("default_purify_comment")
    assert scenario is not None, "default_purify_comment must be seeded by migration 009"
    redis = await get_redis()
    await redis.delete(lead_tracker._welcome_key(user["id"]))
    return user, conv, scenario


@pytest.mark.asyncio
async def test_returns_message_with_reply_to_comment_id(db) -> None:
    user, conv, scenario = await _setup(db, "ctdm_1")
    event = _make_comment_event(external_user_id="ctdm_1")

    msg = await handle_comment_to_dm(event, user, conv, scenario)

    assert msg is not None
    assert msg.reply_to_comment_id == "comment_1"
    assert "Маша" in msg.text
    assert f"ig_{user['short_id']}_purify" in msg.text


@pytest.mark.asyncio
async def test_idempotency_per_user_post(db) -> None:
    user, conv, scenario = await _setup(db, "ctdm_idem")

    # First comment
    e1 = _make_comment_event(
        external_user_id="ctdm_idem",
        comment_id="c_1",
        text="ОЧИЩЕНИЕ",
    )
    msg1 = await handle_comment_to_dm(e1, user, conv, scenario)
    assert msg1 is not None

    # Second comment under SAME post — should be skipped
    e2 = _make_comment_event(
        external_user_id="ctdm_idem",
        comment_id="c_2",
        text="ОЧИЩЕНИЕ еще",
    )
    msg2 = await handle_comment_to_dm(e2, user, conv, scenario)
    assert msg2 is None


@pytest.mark.asyncio
async def test_different_post_not_blocked(db) -> None:
    """A user can comment under multiple posts and get DM each time
    (until welcome flag locks them out)."""
    user, conv, scenario = await _setup(db, "ctdm_diff_post")

    e1 = _make_comment_event(
        external_user_id="ctdm_diff_post",
        post_id="post_A",
        comment_id="c_a",
    )
    msg1 = await handle_comment_to_dm(e1, user, conv, scenario)
    assert msg1 is not None

    # Reset welcome flag to test post-isolation only
    redis = await get_redis()
    await redis.delete(lead_tracker._welcome_key(user["id"]))

    e2 = _make_comment_event(
        external_user_id="ctdm_diff_post",
        post_id="post_B",
        comment_id="c_b",
    )
    msg2 = await handle_comment_to_dm(e2, user, conv, scenario)
    assert msg2 is not None  # different post → allowed


@pytest.mark.asyncio
async def test_welcome_flag_blocks_comment_dm(db) -> None:
    """If user already received welcome (via DM or earlier comment),
    new comment doesn't trigger another DM."""
    user, conv, scenario = await _setup(db, "ctdm_welcome_block")
    await lead_tracker.mark_welcome_sent(user["id"])

    event = _make_comment_event(external_user_id="ctdm_welcome_block")
    msg = await handle_comment_to_dm(event, user, conv, scenario)
    assert msg is None

    # And the comment is still marked as deduped, so engine won't retry it
    deduped = await ct_repo.already_replied(
        user_id=user["id"],
        platform="instagram",
        post_id="post_1",
        scenario_id=scenario["id"],
    )
    assert deduped is True


@pytest.mark.asyncio
async def test_comment_to_dm_refuses_non_comment(db) -> None:
    user, conv, scenario = await _setup(db, "ctdm_wrong_type")
    # Build a DM event by mistake
    event = IncomingEvent(
        provider="sendpulse",
        platform="instagram",
        event_type="message",  # WRONG TYPE
        external_user_id="ctdm_wrong_type",
        external_event_id="evt_wrong",
        text="hello",
        occurred_at=datetime.now(UTC),
    )
    msg = await handle_comment_to_dm(event, user, conv, scenario)
    assert msg is None


@pytest.mark.asyncio
async def test_comment_to_dm_missing_post_id(db) -> None:
    user, conv, scenario = await _setup(db, "ctdm_no_post")
    event = IncomingEvent(
        provider="sendpulse",
        platform="instagram",
        event_type="comment",
        external_user_id="ctdm_no_post",
        external_event_id="evt_np",
        text="ОЧИЩЕНИЕ",
        # post_id and comment_id missing — malformed event
        occurred_at=datetime.now(UTC),
    )
    msg = await handle_comment_to_dm(event, user, conv, scenario)
    assert msg is None
```

c) Создать `tests/test_engine_comment_routing.py`:

```python
"""Tests for ScenarioEngine routing of comment events.

Verifies the lookup precedence:
1. comment_triggers (post-specific) wins over keywords (global)
2. keywords act as fallback
"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.models.events import IncomingEvent
from app.repos import conversations, users
from app.services import scenario_engine
from app.services.keyword_matcher import reset_cache


@pytest.fixture(autouse=True)
def _reset() -> None:
    reset_cache()
    yield
    reset_cache()


def _comment_event(
    text: str = "ОЧИЩЕНИЕ",
    post_id: str = "post_X",
    external_user_id: str = "engine_c_1",
) -> IncomingEvent:
    return IncomingEvent(
        provider="sendpulse",
        platform="instagram",
        event_type="comment",
        external_user_id=external_user_id,
        external_event_id=f"evt_ec_{external_user_id}",
        text=text,
        post_id=post_id,
        comment_id=f"c_{external_user_id}",
        full_name="Test",
        occurred_at=datetime(2026, 4, 30, 12, 0, tzinfo=UTC),
    )


@pytest.mark.asyncio
async def test_global_keyword_routes_to_default_scenario(db) -> None:
    """Without a comment_triggers row, the global 'очищение' keyword
    (seeded by migration 009) routes to default_purify_comment."""
    user = await users.create(
        provider_name="sendpulse", platform="instagram", external_id="engine_global_kw",
    )
    conv = await conversations.create(user["id"], "instagram")

    event = _comment_event(text="хочу очищение программу", external_user_id="engine_global_kw")
    msg = await scenario_engine.handle(event, user, conv, is_new_user=True)

    assert msg is not None
    assert msg.reply_to_comment_id == event.comment_id
    # Slug is 'purify' from default scenario metadata
    assert f"ig_{user['short_id']}_purify" in msg.text


@pytest.mark.asyncio
async def test_post_specific_trigger_overrides_global(db) -> None:
    """comment_triggers row takes precedence over global keywords."""
    # Create a custom scenario with slug='oils'
    custom_row = await db.fetchrow(
        """
        INSERT INTO scenarios (name, type, template, metadata, active)
        VALUES (
            'oils_for_specific_post',
            'comment_to_dm',
            E'Привет, {first_name}! {tg_link}\n{disclaimer}',
            '{"tg_scenario_slug": "oils"}'::jsonb,
            TRUE
        )
        RETURNING *
        """
    )
    # Pin it to a specific post_id with keyword 'очищение'
    await db.execute(
        """
        INSERT INTO comment_triggers (platform, post_id, keyword, scenario_id, active)
        VALUES ('instagram', 'reels_oils', 'очищение', $1, TRUE)
        """,
        custom_row["id"],
    )

    user = await users.create(
        provider_name="sendpulse", platform="instagram", external_id="engine_override",
    )
    conv = await conversations.create(user["id"], "instagram")

    event = _comment_event(
        text="ОЧИЩЕНИЕ",
        post_id="reels_oils",
        external_user_id="engine_override",
    )
    msg = await scenario_engine.handle(event, user, conv, is_new_user=True)

    assert msg is not None
    # Slug should be 'oils' from the post-specific override, not 'purify' from global
    assert f"ig_{user['short_id']}_oils" in msg.text
```

d) Создать `tests/test_e2e_comment_to_dm.py`:

```python
"""E2E test: webhook with comment event → comment-to-DM scenario → DM with deep-link."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest
from httpx import AsyncClient

from app.models.events import IncomingEvent
from app.repos import users
from app.workers.tasks_messages import process_incoming_event
from tests.fakes.fake_provider import FakeProvider


@pytest.mark.asyncio
async def test_full_pipeline_comment_to_dm(
    client: AsyncClient,
    fake_provider: FakeProvider,
    db,
) -> None:
    event = IncomingEvent(
        provider="sendpulse",
        platform="instagram",
        event_type="comment",
        external_user_id="e2e_ctdm_user",
        external_event_id="e2e_ctdm_evt",
        username="masha_p",
        full_name="Маша Петрова",
        text="ОЧИЩЕНИЕ пожалуйста!",
        post_id="reels_42",
        comment_id="comment_42",
        occurred_at=datetime(2026, 4, 30, 12, 0, tzinfo=UTC),
    )
    fake_provider.queue_event(event)

    # POST webhook
    response = await client.post("/webhooks/sendpulse", json={})
    assert response.status_code == 200

    log_row = await db.fetchrow(
        "SELECT * FROM events_log WHERE external_event_id = $1",
        "e2e_ctdm_evt",
    )
    assert log_row is not None

    # Drive worker
    await process_incoming_event({}, event.model_dump(mode="json"), log_row["id"])

    # Verify user was created
    user = await users.get_by_external("sendpulse", "instagram", "e2e_ctdm_user")
    assert user is not None

    # Verify outgoing message has reply_to_comment_id set in provider's queue
    assert len(fake_provider.sent) == 1
    sent = fake_provider.sent[0]
    assert sent.reply_to_comment_id == "comment_42"
    assert sent.text is not None
    assert "Маша" in sent.text
    assert f"ig_{user['short_id']}_purify" in sent.text

    # Verify DB has the outgoing message recorded
    msgs = await db.fetch(
        """
        SELECT m.* FROM messages m
        JOIN conversations c ON c.id = m.conversation_id
        WHERE c.user_id = $1 AND m.direction = 'out'
        """,
        user["id"],
    )
    assert len(msgs) == 1
    assert msgs[0]["scenario_id"] is not None

    # Verify dedup record was created
    dedup = await db.fetchrow(
        """
        SELECT * FROM comment_user_dedup
        WHERE user_id = $1 AND post_id = 'reels_42'
        """,
        user["id"],
    )
    assert dedup is not None
```

---

## Acceptance criteria

После выполнения всех подзадач выполнить и поставить галочки:

- [ ] Файлы созданы по структуре подзадач 1–6
- [ ] Миграции 008 и 009 применены: `docker compose exec postgres psql -U social_inbox -d social_inbox -c "\d comment_user_dedup"` показывает таблицу
- [ ] Сценарий `default_purify_comment` посеян: `SELECT name, type, metadata FROM scenarios WHERE name='default_purify_comment'` возвращает строку
- [ ] Глобальный keyword «очищение» посеян: `SELECT keyword, context, scenario_id FROM keywords WHERE keyword='очищение'` возвращает строку
- [ ] `make lint` проходит без ошибок
- [ ] `make test` проходит, все новые тесты зелёные:
  - `test_repos_comment_triggers.py` — 6 тестов
  - `test_scenario_comment_to_dm.py` — 6 тестов
  - `test_engine_comment_routing.py` — 2 теста
  - `test_e2e_comment_to_dm.py` — 1 ключевой тест
  - Все существующие тесты Tasks 01, 03, 04, 06, 07, 08 продолжают работать
- [ ] Ручная проверка через FakeProvider в Python shell:
  ```python
  # POST с event_type='comment' + post_id + comment_id
  # → провайдер получает OutgoingMessage с reply_to_comment_id заполненным
  ```
- [ ] Идемпотентность: 5 раз POST с одним и тем же `comment_id` или с разными `comment_id` от того же user под тем же post → ровно 1 запись в `comment_user_dedup` и 1 запись в исходящих `messages`
- [ ] CLAUDE.md (опционально, но желательно): в § 17 в roadmap отметить Task 09 как выполненный

---

## Do NOT

- НЕ добавлять `reply_to_comment` метод в MessagingProvider ABC. Это Task 05 (там SendPulseProvider будет реализовывать send + reply_to_comment в зависимости от наличия `OutgoingMessage.reply_to_comment_id`).
- НЕ реализовывать public reply на сам комментарий. Только log + TODO. Полная реализация — Task 05.
- НЕ изменять формат `OutgoingMessage` модели. Поле `reply_to_comment_id` уже есть из Task 04.
- НЕ создавать отдельную conversation для comments. Comments и DMs от одного юзера на одной платформе — один логический conversation.
- НЕ удалять Redis welcome-flag после comment-to-DM. Re-use оставляет user в едином state «уже получил deep-link».
- НЕ хранить `post_id` как INT — у Instagram это строка с буквами и подчёркиваниями.
- НЕ применять safety-фильтры к comment-to-DM шаблону. Шаблон в БД, контролируется админом, доверенный.
- НЕ открывать новый канал общения в Instagram через комментарий — мы используем стандартный private reply API. Ограничения SendPulse: private reply возможен только в течение 7 дней с момента комментария. Для Task 09 нам это не критично — обработка идёт в реальном времени через webhook.
- НЕ добавлять зависимости вне списка из Task 01.

---

## Зависимости задачи

- Tasks 01, 03, 04, 06, 07, 08 применены
- Task 05 (SendPulseProvider) НЕ требуется — тесты идут через FakeProvider, в проде же `reply_to_comment_id` будет правильно использоваться когда Task 05 реализует SendPulse private reply API.

---

## Что после этой задачи

После применения Task 09 у нас работают **оба основных acquisition-сценария** Юли:

1. Прямой DM → welcome
2. Комментарий с keyword → comment-to-DM

Дальше:

- **Task 10** уже включена в Task 08 (lead_tracker) — пропускаем
- **Task 11** — `/api/lead/{short_id}` endpoint для bot_purify. Это критическая задача — после неё **впервые работает реальный e2e через bot_purify**, не FakeProvider.
- **Task 13** — Claude integration: умные ответы на FAQ-сообщения после welcome. Превращает echo fallback в смысловые ответы через AI.

---

**Дата создания:** 2026-04-30
**Применять в:** `D:\Work\social_inbox` после Tasks 01, 03, 04, 06, 07, 08
**Эстимейт:** 4 часа на Claude Code + ручная проверка
