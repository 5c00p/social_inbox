# Task 15: Admin dashboard

> Применить в `D:\Work\social_inbox` после Tasks 01, 03, 04, 06, 07, 08, 09, 11, 13, 14. Положить файл в корень проекта, открыть в VS Code, попросить Claude Code: «Прочитай TASK_15_admin_dashboard.md и выполни все подзадачи последовательно. Перед каждой подзадачей кратко опиши план, после — что фактически сделано».

---

## Контекст

После Task 14 у нас функциональная воронка с safety-фильтрами. Но **Юля не имеет инструмента управления**:
- Не может посмотреть, кто сейчас в handover
- Не может ответить пользователю, которому Claude эскалировал диалог
- Не может добавить новое keyword «МАСЛА» под свежий Reels — приходится дёргать Виктора
- Не видит метрики: сколько лидов пришло вчера, какой % дошёл до Telegram

Эта задача даёт Юле автономию: дашборд на отдельном поддомене, простой UI, всё что нужно для оперативной работы с воронкой.

**Ключевое архитектурное решение — Streamlit, не FastAPI/HTML.** Аргументы:
- Юля не разработчик; Streamlit рендерит формы и таблицы одной строкой Python
- Можно добавлять страницы за минуты, без вёрстки
- Изоляция от прода: админка упадёт → core-flow работает
- Авто-перезагрузка после изменения кода — быстрая итерация

Минус — отдельный контейнер. Это приемлемо.

---

## Цель

После выполнения этой задачи:

- Запущен Streamlit на порту 8501 в отдельном контейнере
- Авторизация через Basic Auth (env: `ADMIN_BASIC_AUTH_USER` + `ADMIN_BASIC_AUTH_PASSWORD`)
- 5 страниц: Inbox, Диалог, Сценарии, Ключевые слова, Статистика
- Юля может: ответить в handover-диалог (запись в `messages` + отправка через провайдер); закрыть handover; добавить/отредактировать/выключить keyword; отредактировать шаблон сценария; отключить smart_mode для пользователя; посмотреть метрики
- Все действия в админке логируются в новой таблице `admin_audit_log`
- Тесты покрывают: data-функции (БД-запросы), формы (mock Streamlit), миграция админ-таблицы
- `make lint` и `make test` зелёные

---

## Подзадачи

### 1. Миграция: admin_audit_log

a) Создать `migrations/012_admin_audit.sql`:

```sql
-- Migration 012: Audit log of admin actions in dashboard.
--
-- Records every mutation done by Yulia (or any admin) through the dashboard.
-- Used for post-incident review ("кто закрыл handover пользователю X?")
-- and for general accountability.
--
-- Read-only access from app code (only admin/ writes here).

CREATE TABLE IF NOT EXISTS admin_audit_log (
    id              BIGSERIAL PRIMARY KEY,
    actor           TEXT NOT NULL,                  -- basic_auth username
    action          TEXT NOT NULL,                  -- e.g. 'reply', 'close_handover', 'keyword_create'
    target_type     TEXT,                           -- 'conversation', 'keyword', 'scenario', 'user'
    target_id       BIGINT,                         -- the affected row id
    details         JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_admin_audit_created
    ON admin_audit_log(created_at DESC);
```

### 2. Структура каталогов

a) Создать каталоги:

```
admin/
├── __init__.py
├── streamlit_app.py
├── auth.py
├── data/
│   ├── __init__.py
│   ├── conversations.py
│   ├── messages.py
│   ├── scenarios.py
│   ├── keywords.py
│   ├── stats.py
│   └── audit.py
├── pages/
│   ├── __init__.py
│   ├── _01_inbox.py
│   ├── _02_conversation.py
│   ├── _03_scenarios.py
│   ├── _04_keywords.py
│   └── _05_stats.py
└── components/
    ├── __init__.py
    └── header.py
```

   Префикс `_NN_` в `pages/` — это стандарт Streamlit: цифры задают порядок в навигации, `_` снимает страницу из автоматической боковой панели (мы рендерим навигацию вручную).

### 3. Зависимости

a) В `pyproject.toml` в `[dependency-groups] dev` добавить streamlit (не в основные deps, чтобы не тащить в worker):

   Создать новый блок:

```toml
[dependency-groups]
dev = [
    "pytest==8.3.4",
    # ... existing dev deps ...
]
admin = [
    "streamlit==1.41.0",
    "altair==5.5.0",
    "pandas==2.2.3",
]
```

b) Обновить uv lock:
```bash
uv lock
```

   В Dockerfile.admin (создаётся в подзадаче 9) будем устанавливать `--group admin`.

### 4. Auth helper

a) Создать `admin/auth.py`:

```python
"""Basic Auth for Streamlit admin dashboard.

Streamlit doesn't have native auth — we wrap each page entry with `require_auth()`.
Credentials checked against ADMIN_BASIC_AUTH_USER and ADMIN_BASIC_AUTH_PASSWORD env vars.

Why not OAuth/SSO: only 1-2 users (Yulia + Victor), single-machine deploy.
Upgrade path: when team grows, swap for streamlit-authenticator with hashed passwords.
"""
from __future__ import annotations

import secrets

import streamlit as st

from app.config import get_settings


def require_auth() -> str:
    """Display login form if not authenticated, else return logged-in username.

    Stops Streamlit execution on the login screen via `st.stop()`.
    """
    settings = get_settings()
    expected_user = settings.admin_basic_auth_user
    expected_pass = settings.admin_basic_auth_password

    if not expected_pass:
        st.error(
            "ADMIN_BASIC_AUTH_PASSWORD не настроен. "
            "Установи в .env и перезапусти контейнер."
        )
        st.stop()

    if st.session_state.get("auth_ok"):
        return st.session_state["auth_user"]

    st.title("🔐 Вход в админку")
    with st.form("login"):
        username = st.text_input("Пользователь")
        password = st.text_input("Пароль", type="password")
        submitted = st.form_submit_button("Войти")

    if submitted:
        if (
            secrets.compare_digest(username, expected_user)
            and secrets.compare_digest(password, expected_pass)
        ):
            st.session_state["auth_ok"] = True
            st.session_state["auth_user"] = username
            st.rerun()
        else:
            st.error("Неверный логин или пароль")

    st.stop()
```

### 5. Data layer (admin/data/)

a) Создать `admin/data/conversations.py`:

```python
"""Read access to conversations + writes for admin actions."""
from __future__ import annotations

from typing import Any

import asyncpg

from app.repos.pool import get_pool


async def list_conversations(
    *,
    status_filter: str | None = None,
    search_username: str | None = None,
    limit: int = 100,
) -> list[asyncpg.Record]:
    """Return conversations enriched with user info, sorted handover-pending first."""
    pool = await get_pool()
    where = ["u.deleted_at IS NULL"]
    params: list[Any] = []
    if status_filter:
        params.append(status_filter)
        where.append(f"c.status = ${len(params)}")
    if search_username:
        params.append(f"%{search_username}%")
        where.append(f"u.username ILIKE ${len(params)}")

    params.append(limit)
    sql = f"""
        SELECT c.id AS conversation_id,
               c.status, c.last_message_at, c.created_at, c.handover_reason,
               u.id AS user_id, u.platform, u.username, u.full_name,
               u.short_id, u.tg_handover_at, u.smart_mode_enabled
        FROM conversations c
        JOIN social_users u ON u.id = c.user_id
        WHERE {' AND '.join(where)}
        ORDER BY
            CASE c.status WHEN 'handover_pending' THEN 0 ELSE 1 END,
            c.last_message_at DESC
        LIMIT ${len(params)}
    """
    return await pool.fetch(sql, *params)


async def get_conversation(conversation_id: int) -> asyncpg.Record | None:
    pool = await get_pool()
    return await pool.fetchrow(
        """
        SELECT c.*, u.platform AS user_platform, u.username, u.full_name,
               u.short_id, u.smart_mode_enabled, u.external_id, u.tg_user_id
        FROM conversations c
        JOIN social_users u ON u.id = c.user_id
        WHERE c.id = $1
        """,
        conversation_id,
    )


async def close_handover(conversation_id: int) -> None:
    pool = await get_pool()
    await pool.execute(
        """
        UPDATE conversations
        SET status = 'handover_done', closed_at = NOW()
        WHERE id = $1
        """,
        conversation_id,
    )


async def reopen_conversation(conversation_id: int) -> None:
    pool = await get_pool()
    await pool.execute(
        """
        UPDATE conversations
        SET status = 'active', closed_at = NULL
        WHERE id = $1
        """,
        conversation_id,
    )


async def set_smart_mode(user_id: int, enabled: bool) -> None:
    pool = await get_pool()
    await pool.execute(
        "UPDATE social_users SET smart_mode_enabled = $2 WHERE id = $1",
        user_id, enabled,
    )
```

b) Создать `admin/data/messages.py`:

```python
"""Message access for admin dashboard."""
from __future__ import annotations

import asyncpg

from app.repos.pool import get_pool


async def get_messages(conversation_id: int, limit: int = 50) -> list[asyncpg.Record]:
    pool = await get_pool()
    return await pool.fetch(
        """
        SELECT id, direction, text, source, scenario_id,
               claude_used, claude_model, claude_tokens_in, claude_tokens_out,
               safety_blocked, safety_reason, created_at
        FROM messages
        WHERE conversation_id = $1
        ORDER BY created_at ASC
        LIMIT $2
        """,
        conversation_id, limit,
    )
```

c) Создать `admin/data/scenarios.py`:

```python
"""Scenarios CRUD for admin."""
from __future__ import annotations

import asyncpg

from app.repos.pool import get_pool


async def list_all() -> list[asyncpg.Record]:
    pool = await get_pool()
    return await pool.fetch(
        """
        SELECT id, name, type, template, metadata, active, updated_at
        FROM scenarios
        ORDER BY type, id
        """
    )


async def get(scenario_id: int) -> asyncpg.Record | None:
    pool = await get_pool()
    return await pool.fetchrow(
        "SELECT * FROM scenarios WHERE id = $1", scenario_id,
    )


async def update_template(scenario_id: int, template: str) -> None:
    pool = await get_pool()
    await pool.execute(
        """
        UPDATE scenarios
        SET template = $2, updated_at = NOW()
        WHERE id = $1
        """,
        scenario_id, template,
    )


async def set_active(scenario_id: int, active: bool) -> None:
    pool = await get_pool()
    await pool.execute(
        """
        UPDATE scenarios SET active = $2, updated_at = NOW() WHERE id = $1
        """,
        scenario_id, active,
    )
```

d) Создать `admin/data/keywords.py`:

```python
"""Keywords CRUD for admin."""
from __future__ import annotations

import asyncpg

from app.repos.pool import get_pool


async def list_all() -> list[asyncpg.Record]:
    pool = await get_pool()
    return await pool.fetch(
        """
        SELECT k.id, k.keyword, k.match_type, k.context, k.scenario_id,
               k.priority, k.case_sensitive, k.active,
               s.name AS scenario_name
        FROM keywords k
        LEFT JOIN scenarios s ON s.id = k.scenario_id
        ORDER BY k.priority ASC, k.id ASC
        """
    )


async def create(
    *,
    keyword: str,
    match_type: str,
    context: str,
    scenario_id: int,
    priority: int = 100,
    case_sensitive: bool = False,
) -> int:
    pool = await get_pool()
    row = await pool.fetchrow(
        """
        INSERT INTO keywords (keyword, match_type, context, scenario_id,
                              priority, case_sensitive, active)
        VALUES ($1, $2, $3, $4, $5, $6, TRUE)
        RETURNING id
        """,
        keyword, match_type, context, scenario_id, priority, case_sensitive,
    )
    return row["id"]


async def update(
    keyword_id: int,
    *,
    keyword: str,
    match_type: str,
    context: str,
    scenario_id: int,
    priority: int,
    case_sensitive: bool,
    active: bool,
) -> None:
    pool = await get_pool()
    await pool.execute(
        """
        UPDATE keywords
        SET keyword=$2, match_type=$3, context=$4, scenario_id=$5,
            priority=$6, case_sensitive=$7, active=$8
        WHERE id = $1
        """,
        keyword_id, keyword, match_type, context, scenario_id,
        priority, case_sensitive, active,
    )


async def delete(keyword_id: int) -> None:
    pool = await get_pool()
    await pool.execute("DELETE FROM keywords WHERE id = $1", keyword_id)
```

e) Создать `admin/data/stats.py`:

```python
"""Aggregate statistics for the dashboard."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import asyncpg

from app.repos.pool import get_pool


async def daily_new_leads(days: int = 14) -> list[asyncpg.Record]:
    """Number of new social_users per day, last N days."""
    pool = await get_pool()
    return await pool.fetch(
        """
        SELECT DATE_TRUNC('day', first_seen_at) AS day,
               COUNT(*)::int AS count
        FROM social_users
        WHERE first_seen_at >= NOW() - $1::interval
          AND deleted_at IS NULL
        GROUP BY day
        ORDER BY day
        """,
        f"{days} days",
    )


async def conversion_to_telegram(days: int = 30) -> dict[str, int]:
    """Of users seen in last N days, how many also have tg_user_id?"""
    pool = await get_pool()
    row = await pool.fetchrow(
        """
        SELECT
            COUNT(*) FILTER (WHERE first_seen_at >= NOW() - $1::interval)::int AS total,
            COUNT(*) FILTER (
                WHERE first_seen_at >= NOW() - $1::interval
                  AND tg_handover_at IS NOT NULL
            )::int AS converted
        FROM social_users
        WHERE deleted_at IS NULL
        """,
        f"{days} days",
    )
    return {"total": row["total"], "converted": row["converted"]}


async def handover_breakdown(days: int = 30) -> list[asyncpg.Record]:
    """How many handovers per source (operator_request, symptom, claude_tool, etc.)."""
    pool = await get_pool()
    return await pool.fetch(
        """
        SELECT
            COALESCE(SPLIT_PART(handover_reason, ':', 1), 'unknown') AS source,
            COUNT(*)::int AS count
        FROM conversations
        WHERE status IN ('handover_pending', 'handover_done')
          AND created_at >= NOW() - $1::interval
        GROUP BY source
        ORDER BY count DESC
        """,
        f"{days} days",
    )


async def claude_token_usage(days: int = 7) -> list[asyncpg.Record]:
    """Daily Claude token cost (input + output)."""
    pool = await get_pool()
    return await pool.fetch(
        """
        SELECT DATE_TRUNC('day', created_at) AS day,
               SUM(claude_tokens_in)::int  AS tokens_in,
               SUM(claude_tokens_out)::int AS tokens_out,
               COUNT(*) FILTER (WHERE claude_used)::int AS messages_count
        FROM messages
        WHERE created_at >= NOW() - $1::interval
          AND claude_used = TRUE
        GROUP BY day
        ORDER BY day
        """,
        f"{days} days",
    )
```

f) Создать `admin/data/audit.py`:

```python
"""Audit log for admin actions."""
from __future__ import annotations

from typing import Any

from app.repos.pool import get_pool


async def record_action(
    *,
    actor: str,
    action: str,
    target_type: str | None = None,
    target_id: int | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    pool = await get_pool()
    await pool.execute(
        """
        INSERT INTO admin_audit_log (actor, action, target_type, target_id, details)
        VALUES ($1, $2, $3, $4, $5)
        """,
        actor, action, target_type, target_id, details or {},
    )


async def recent(limit: int = 50) -> list:
    pool = await get_pool()
    return await pool.fetch(
        """
        SELECT actor, action, target_type, target_id, details, created_at
        FROM admin_audit_log
        ORDER BY created_at DESC
        LIMIT $1
        """,
        limit,
    )
```

### 6. Заголовок (header)

a) Создать `admin/components/header.py`:

```python
"""Reusable header with logout button."""
from __future__ import annotations

import streamlit as st


def render(actor: str) -> None:
    cols = st.columns([4, 1])
    with cols[0]:
        st.caption(f"Вошла как: **{actor}**")
    with cols[1]:
        if st.button("Выйти", use_container_width=True):
            st.session_state.clear()
            st.rerun()
```

### 7. Главный entry point

a) Создать `admin/streamlit_app.py`:

```python
"""Admin dashboard for social_inbox.

Run:
    streamlit run admin/streamlit_app.py --server.port 8501

Pages are dispatched manually from a sidebar radio (instead of Streamlit's
auto-discovery from pages/ directory) so we can hide unauthorized pages.
"""
from __future__ import annotations

import asyncio

import streamlit as st

from admin.auth import require_auth
from admin.components import header
from admin.pages import _01_inbox, _02_conversation, _03_scenarios, _04_keywords, _05_stats

st.set_page_config(
    page_title="social_inbox · admin",
    page_icon="💚",
    layout="wide",
)

PAGES = {
    "📥 Входящие": _01_inbox.render,
    "💬 Диалог": _02_conversation.render,
    "🎬 Сценарии": _03_scenarios.render,
    "🔑 Ключевые слова": _04_keywords.render,
    "📊 Статистика": _05_stats.render,
}


def main() -> None:
    actor = require_auth()

    with st.sidebar:
        st.title("social_inbox")
        page_label = st.radio(
            "Раздел",
            list(PAGES.keys()),
            label_visibility="collapsed",
            key="page_selector",
        )

    header.render(actor)
    PAGES[page_label](actor=actor)


if __name__ == "__main__":
    main()
```

### 8. Страницы

a) Создать `admin/pages/_01_inbox.py`:

```python
"""Inbox page: list conversations, handover-pending first."""
from __future__ import annotations

import asyncio

import streamlit as st

from admin.data import conversations as conv_data


def render(actor: str) -> None:
    st.header("📥 Входящие диалоги")

    cols = st.columns([2, 2, 1])
    with cols[0]:
        status_filter = st.selectbox(
            "Статус",
            options=["Все", "active", "handover_pending", "handover_done", "closed"],
            index=0,
        )
    with cols[1]:
        search = st.text_input("Поиск по username", placeholder="например: masha_p")
    with cols[2]:
        if st.button("🔄 Обновить", use_container_width=True):
            st.rerun()

    rows = asyncio.run(conv_data.list_conversations(
        status_filter=None if status_filter == "Все" else status_filter,
        search_username=search or None,
        limit=200,
    ))

    if not rows:
        st.info("Пока пусто.")
        return

    for row in rows:
        status_badge = _status_badge(row["status"])
        smart_badge = "" if row["smart_mode_enabled"] else " 🚫AI"
        title = (
            f"{status_badge} **@{row['username'] or '(нет)'}** "
            f"({row['full_name'] or '—'}) · {row['platform']}{smart_badge}"
        )

        with st.container(border=True):
            cols = st.columns([5, 1])
            with cols[0]:
                st.markdown(title)
                if row["handover_reason"]:
                    st.caption(f"📌 {row['handover_reason']}")
                st.caption(
                    f"Последнее: {row['last_message_at'].strftime('%Y-%m-%d %H:%M') if row['last_message_at'] else '—'}"
                    f" · short_id: `{row['short_id']}`"
                )
            with cols[1]:
                if st.button("Открыть", key=f"open_{row['conversation_id']}", use_container_width=True):
                    st.session_state["selected_conversation_id"] = row["conversation_id"]
                    st.session_state["page_selector"] = "💬 Диалог"
                    st.rerun()


def _status_badge(status: str) -> str:
    return {
        "active": "🟢",
        "handover_pending": "🔴",
        "handover_done": "✅",
        "closed": "⚪",
    }.get(status, "❔")
```

b) Создать `admin/pages/_02_conversation.py`:

```python
"""Conversation detail page: history + reply form + handover controls."""
from __future__ import annotations

import asyncio

import streamlit as st

from admin.data import audit, conversations as conv_data, messages as msg_data
from app.models.events import OutgoingMessage
from app.providers import get_provider
from app.repos import messages as messages_repo


def render(actor: str) -> None:
    conv_id = st.session_state.get("selected_conversation_id")
    if not conv_id:
        st.info("Выбери диалог во вкладке «Входящие».")
        return

    conv = asyncio.run(conv_data.get_conversation(conv_id))
    if not conv:
        st.error("Диалог не найден.")
        return

    st.header(f"💬 Диалог с @{conv['username'] or '(нет username)'}")
    st.caption(
        f"{conv['full_name'] or '—'} · {conv['user_platform']} · "
        f"short_id: `{conv['short_id']}` · TG: {conv['tg_user_id'] or '—'}"
    )

    # Status + actions row
    c1, c2, c3 = st.columns(3)
    with c1:
        st.metric("Статус", conv["status"])
    with c2:
        st.metric("AI режим", "вкл" if conv["smart_mode_enabled"] else "выкл")
    with c3:
        st.metric("Платформа", conv["user_platform"])

    if conv["handover_reason"]:
        st.warning(f"Причина handover: {conv['handover_reason']}")

    # Message history
    st.subheader("История сообщений")
    msgs = asyncio.run(msg_data.get_messages(conv_id, limit=100))
    for m in msgs:
        with st.chat_message("user" if m["direction"] == "in" else "assistant"):
            text = m["text"] or "_(пусто)_"
            if m["safety_blocked"]:
                text = f"🛑 _Заблокировано: {m['safety_reason']}_"
            st.write(text)
            cap = m["created_at"].strftime("%Y-%m-%d %H:%M")
            if m["claude_used"]:
                cap += f" · claude {m['claude_model']} ({m['claude_tokens_in']}+{m['claude_tokens_out']})"
            if m["scenario_id"]:
                cap += f" · scenario #{m['scenario_id']}"
            st.caption(cap)

    # Reply form
    st.subheader("Ответ от Юли")
    with st.form("reply_form", clear_on_submit=True):
        reply_text = st.text_area("Текст", height=100, placeholder="Напиши сообщение пользователю...")
        submit = st.form_submit_button("Отправить")
    if submit and reply_text.strip():
        asyncio.run(_send_admin_reply(conv, reply_text.strip(), actor))
        st.success("Отправлено.")
        st.rerun()

    # Handover / smart-mode controls
    st.subheader("Управление")
    col1, col2, col3 = st.columns(3)
    with col1:
        if conv["status"] == "handover_pending":
            if st.button("✅ Закрыть handover", use_container_width=True):
                asyncio.run(conv_data.close_handover(conv_id))
                asyncio.run(audit.record_action(
                    actor=actor, action="close_handover",
                    target_type="conversation", target_id=conv_id,
                ))
                st.rerun()
        else:
            if st.button("⚠️ Перевести в handover", use_container_width=True):
                # Manual handover flow: use existing handover service
                from app.services import handover as ho
                from app.repos import users as users_repo, conversations as convs_repo
                user = asyncio.run(users_repo.get_by_short_id(conv["short_id"]))
                # Re-fetch as Record-like for the service
                conv_full = asyncio.run(convs_repo.get_active(user["id"], conv["user_platform"]))
                if conv_full is None:
                    # fallback: fetch directly
                    from app.repos.pool import get_pool
                    pool = asyncio.run(get_pool())
                    conv_full = asyncio.run(pool.fetchrow(
                        "SELECT * FROM conversations WHERE id = $1", conv_id,
                    ))
                asyncio.run(ho.trigger_handover(
                    conversation=conv_full, user=user,
                    source="manual",
                    reason=f"manual by {actor}",
                ))
                asyncio.run(audit.record_action(
                    actor=actor, action="trigger_handover",
                    target_type="conversation", target_id=conv_id,
                ))
                st.rerun()
    with col2:
        new_smart = st.toggle(
            "AI режим включён",
            value=conv["smart_mode_enabled"],
            help="Если выключить — Claude больше не будет отвечать этому пользователю.",
        )
        if new_smart != conv["smart_mode_enabled"]:
            asyncio.run(conv_data.set_smart_mode(conv["user_id"], new_smart))
            asyncio.run(audit.record_action(
                actor=actor, action="set_smart_mode",
                target_type="user", target_id=conv["user_id"],
                details={"enabled": new_smart},
            ))
            st.rerun()


async def _send_admin_reply(conv, text: str, actor: str) -> None:
    """Send a manual reply through the active provider and persist as outgoing message."""
    provider = get_provider()
    msg = OutgoingMessage(
        platform=conv["user_platform"],
        external_user_id=conv["external_id"],
        text=text,
        scenario_id=None,  # admin-authored, no scenario
    )
    external_id = None
    try:
        external_id = await provider.send(msg)
    except Exception as exc:  # noqa: BLE001
        st.error(f"Не удалось отправить через провайдер: {exc}")

    import uuid
    record_external_id = external_id if external_id else f"admin:{uuid.uuid4()}"
    await messages_repo.insert(
        conversation_id=conv["id"],
        direction="out",
        text=text,
        source="admin_reply",
        external_message_id=record_external_id,
    )
    await audit.record_action(
        actor=actor, action="reply",
        target_type="conversation", target_id=conv["id"],
        details={"length": len(text), "send_ok": external_id is not None},
    )
```

c) Создать `admin/pages/_03_scenarios.py`:

```python
"""Scenarios page: edit templates, toggle active."""
from __future__ import annotations

import asyncio

import streamlit as st

from admin.data import audit, scenarios as sc_data


def render(actor: str) -> None:
    st.header("🎬 Сценарии")
    st.caption(
        "Welcome / comment-to-DM / handover — это шаблоны автоответов. "
        "Можно изменить текст без перезапуска. "
        "`{first_name}`, `{tg_link}`, `{disclaimer}` — подставляются автоматически."
    )

    rows = asyncio.run(sc_data.list_all())
    if not rows:
        st.info("Сценариев пока нет.")
        return

    for row in rows:
        with st.expander(
            f"**{row['name']}** · {row['type']} · {'🟢 активен' if row['active'] else '⚪ выключен'}"
        ):
            with st.form(f"scenario_{row['id']}"):
                template = st.text_area(
                    "Шаблон",
                    value=row["template"] or "",
                    height=180,
                    help=(
                        "Доступные плейсхолдеры: {first_name} {tg_link} {disclaimer}\n"
                        "Smart-сценарий не использует шаблон — Claude генерирует ответ сам."
                    ),
                )
                active = st.checkbox("Активен", value=row["active"])

                col1, col2 = st.columns([1, 4])
                with col1:
                    save = st.form_submit_button("💾 Сохранить", use_container_width=True)
                with col2:
                    if row["metadata"]:
                        st.caption(f"metadata: `{dict(row['metadata'])}`")

                if save:
                    if template != (row["template"] or ""):
                        asyncio.run(sc_data.update_template(row["id"], template))
                        asyncio.run(audit.record_action(
                            actor=actor, action="scenario_update_template",
                            target_type="scenario", target_id=row["id"],
                            details={"new_length": len(template)},
                        ))
                    if active != row["active"]:
                        asyncio.run(sc_data.set_active(row["id"], active))
                        asyncio.run(audit.record_action(
                            actor=actor, action="scenario_set_active",
                            target_type="scenario", target_id=row["id"],
                            details={"active": active},
                        ))
                    st.success("Сохранено.")
                    st.rerun()
```

d) Создать `admin/pages/_04_keywords.py`:

```python
"""Keywords page: list + create + edit + delete."""
from __future__ import annotations

import asyncio

import streamlit as st

from admin.data import audit, keywords as kw_data, scenarios as sc_data


def render(actor: str) -> None:
    st.header("🔑 Ключевые слова")
    st.caption(
        "Когда подписчик пишет ключевое слово в комментарии или DM — "
        "запускается соответствующий сценарий. Приоритет: меньше = выше."
    )

    scenarios = asyncio.run(sc_data.list_all())
    scenario_options = {s["id"]: f"{s['name']} ({s['type']})" for s in scenarios}

    # New keyword form
    with st.expander("➕ Добавить keyword"):
        with st.form("new_keyword"):
            cols = st.columns([2, 1, 1])
            with cols[0]:
                kw = st.text_input("Слово/фраза", placeholder="например: МАСЛА")
            with cols[1]:
                match_type = st.selectbox("Тип", ["contains", "exact", "regex"])
            with cols[2]:
                context = st.selectbox("Где", ["dm", "comment", "both"])

            cols2 = st.columns([2, 1, 1])
            with cols2[0]:
                scenario_id = st.selectbox(
                    "Сценарий",
                    options=list(scenario_options.keys()),
                    format_func=lambda i: scenario_options[i],
                )
            with cols2[1]:
                priority = st.number_input("Приоритет", value=100, min_value=1, max_value=999)
            with cols2[2]:
                case_sensitive = st.checkbox("Учитывать регистр", value=False)

            if st.form_submit_button("Создать"):
                if not kw.strip():
                    st.error("Слово не может быть пустым.")
                else:
                    new_id = asyncio.run(kw_data.create(
                        keyword=kw.strip(),
                        match_type=match_type,
                        context=context,
                        scenario_id=scenario_id,
                        priority=priority,
                        case_sensitive=case_sensitive,
                    ))
                    asyncio.run(audit.record_action(
                        actor=actor, action="keyword_create",
                        target_type="keyword", target_id=new_id,
                        details={"keyword": kw.strip(), "scenario_id": scenario_id},
                    ))
                    st.success(f"Создан keyword #{new_id}.")
                    st.rerun()

    # List
    rows = asyncio.run(kw_data.list_all())
    for row in rows:
        with st.expander(
            f"**{row['keyword']}** ({row['match_type']}/{row['context']}) → "
            f"{row['scenario_name']} · prio {row['priority']} · "
            f"{'🟢' if row['active'] else '⚪'}"
        ):
            with st.form(f"kw_{row['id']}"):
                cols = st.columns([2, 1, 1])
                with cols[0]:
                    new_kw = st.text_input("Слово/фраза", value=row["keyword"])
                with cols[1]:
                    new_mt = st.selectbox(
                        "Тип", ["contains", "exact", "regex"],
                        index=["contains", "exact", "regex"].index(row["match_type"]),
                    )
                with cols[2]:
                    new_ctx = st.selectbox(
                        "Где", ["dm", "comment", "both"],
                        index=["dm", "comment", "both"].index(row["context"]),
                    )

                cols2 = st.columns([2, 1, 1])
                with cols2[0]:
                    new_sid = st.selectbox(
                        "Сценарий",
                        options=list(scenario_options.keys()),
                        index=list(scenario_options.keys()).index(row["scenario_id"]),
                        format_func=lambda i: scenario_options[i],
                    )
                with cols2[1]:
                    new_prio = st.number_input("Приоритет", value=row["priority"], min_value=1, max_value=999)
                with cols2[2]:
                    new_cs = st.checkbox("Учитывать регистр", value=row["case_sensitive"])

                new_active = st.checkbox("Активен", value=row["active"])

                bcols = st.columns([1, 1, 4])
                with bcols[0]:
                    save = st.form_submit_button("💾 Сохранить")
                with bcols[1]:
                    delete = st.form_submit_button("🗑 Удалить", type="secondary")

                if save:
                    asyncio.run(kw_data.update(
                        row["id"],
                        keyword=new_kw, match_type=new_mt, context=new_ctx,
                        scenario_id=new_sid, priority=new_prio,
                        case_sensitive=new_cs, active=new_active,
                    ))
                    asyncio.run(audit.record_action(
                        actor=actor, action="keyword_update",
                        target_type="keyword", target_id=row["id"],
                    ))
                    st.success("Сохранено.")
                    st.rerun()
                if delete:
                    asyncio.run(kw_data.delete(row["id"]))
                    asyncio.run(audit.record_action(
                        actor=actor, action="keyword_delete",
                        target_type="keyword", target_id=row["id"],
                        details={"keyword": row["keyword"]},
                    ))
                    st.success("Удалено.")
                    st.rerun()
```

e) Создать `admin/pages/_05_stats.py`:

```python
"""Stats page: simple charts and counters."""
from __future__ import annotations

import asyncio

import pandas as pd
import streamlit as st

from admin.data import stats as stats_data


def render(actor: str) -> None:
    st.header("📊 Статистика")

    # Conversion
    conv = asyncio.run(stats_data.conversion_to_telegram(days=30))
    rate = (conv["converted"] / conv["total"] * 100) if conv["total"] else 0
    cols = st.columns(3)
    cols[0].metric("Лидов за 30 дней", conv["total"])
    cols[1].metric("Дошли до Telegram", conv["converted"])
    cols[2].metric("Conversion rate", f"{rate:.1f}%")

    # Daily new leads chart
    st.subheader("Новые лиды по дням")
    daily = asyncio.run(stats_data.daily_new_leads(days=14))
    if daily:
        df = pd.DataFrame([{"day": r["day"], "count": r["count"]} for r in daily])
        df["day"] = pd.to_datetime(df["day"])
        st.line_chart(df.set_index("day"))
    else:
        st.info("Пока нет данных за период.")

    # Handover sources
    st.subheader("Эскалации по причинам (30 дней)")
    breakdown = asyncio.run(stats_data.handover_breakdown(days=30))
    if breakdown:
        df_b = pd.DataFrame([{"source": r["source"], "count": r["count"]} for r in breakdown])
        st.bar_chart(df_b.set_index("source"))
    else:
        st.info("Эскалаций пока не было.")

    # Claude tokens
    st.subheader("Расход Claude (7 дней)")
    tokens = asyncio.run(stats_data.claude_token_usage(days=7))
    if tokens:
        df_t = pd.DataFrame([{
            "day": r["day"],
            "tokens_in": r["tokens_in"] or 0,
            "tokens_out": r["tokens_out"] or 0,
            "messages": r["messages_count"],
        } for r in tokens])
        df_t["day"] = pd.to_datetime(df_t["day"])
        st.dataframe(df_t.set_index("day"), use_container_width=True)
    else:
        st.info("Claude пока не использовался.")
```

### 9. Docker

a) Создать `docker/Dockerfile.admin`:

```dockerfile
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:0.5.13 /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock ./

# Install runtime + admin extras (streamlit, pandas, altair)
RUN uv sync --frozen --no-dev --group admin --no-install-project

COPY app/ ./app/
COPY admin/ ./admin/
COPY migrations/ ./migrations/

RUN uv sync --frozen --no-dev --group admin

EXPOSE 8501

CMD ["uv", "run", "streamlit", "run", "admin/streamlit_app.py",
     "--server.port", "8501",
     "--server.address", "0.0.0.0",
     "--server.headless", "true",
     "--browser.gatherUsageStats", "false"]
```

b) В `docker-compose.yml` добавить сервис `admin`:

```yaml
  admin:
    build:
      context: .
      dockerfile: docker/Dockerfile.admin
    restart: unless-stopped
    env_file: .env
    ports:
      - "8501:8501"
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy
    volumes:
      - ./app:/app/app
      - ./admin:/app/admin
    networks:
      - default
```

   В прод-deploy (Task 17) Traefik будет проксировать `https://inbox-admin.<domain>` → `admin:8501`. Локально доступно на `http://localhost:8501`.

### 10. Тесты

a) Создать `tests/test_admin_data_conversations.py`:

```python
"""Tests for admin data layer — conversations."""
from __future__ import annotations

import pytest

from admin.data import conversations as conv_data
from app.repos import conversations as conv_repo, users


@pytest.mark.asyncio
async def test_list_conversations_handover_first(db) -> None:
    u_active = await users.create(
        provider_name="sendpulse", platform="instagram",
        external_id="ad_active", username="active_u",
    )
    c_active = await conv_repo.create(u_active["id"], "instagram")

    u_handover = await users.create(
        provider_name="sendpulse", platform="instagram",
        external_id="ad_handover", username="handover_u",
    )
    c_handover = await conv_repo.create(u_handover["id"], "instagram")
    await conv_repo.set_status(c_handover["id"], "handover_pending", reason="test")

    rows = await conv_data.list_conversations(limit=100)
    # First row should be the handover_pending one
    statuses_in_order = [r["status"] for r in rows]
    pending_index = statuses_in_order.index("handover_pending")
    active_index = statuses_in_order.index("active")
    assert pending_index < active_index


@pytest.mark.asyncio
async def test_list_conversations_filter_by_status(db) -> None:
    u = await users.create(
        provider_name="sendpulse", platform="instagram",
        external_id="ad_filter",
    )
    await conv_repo.create(u["id"], "instagram")

    rows = await conv_data.list_conversations(status_filter="closed")
    assert all(r["status"] == "closed" for r in rows)


@pytest.mark.asyncio
async def test_close_handover_changes_status(db) -> None:
    u = await users.create(
        provider_name="sendpulse", platform="instagram",
        external_id="ad_close",
    )
    c = await conv_repo.create(u["id"], "instagram")
    await conv_repo.set_status(c["id"], "handover_pending", reason="x")

    await conv_data.close_handover(c["id"])

    row = await db.fetchrow("SELECT * FROM conversations WHERE id = $1", c["id"])
    assert row["status"] == "handover_done"
    assert row["closed_at"] is not None


@pytest.mark.asyncio
async def test_set_smart_mode(db) -> None:
    u = await users.create(
        provider_name="sendpulse", platform="instagram",
        external_id="ad_smart",
    )
    await conv_data.set_smart_mode(u["id"], False)
    refreshed = await db.fetchrow(
        "SELECT smart_mode_enabled FROM social_users WHERE id = $1", u["id"],
    )
    assert refreshed["smart_mode_enabled"] is False
```

b) Создать `tests/test_admin_data_keywords.py`:

```python
"""Tests for admin data layer — keywords CRUD."""
from __future__ import annotations

import pytest

from admin.data import keywords as kw_data


async def _seed_scenario(db, name: str = "kw_test") -> int:
    row = await db.fetchrow(
        "INSERT INTO scenarios (name, type, active) VALUES ($1, 'echo', TRUE) RETURNING id",
        name,
    )
    return row["id"]


@pytest.mark.asyncio
async def test_create_and_list(db) -> None:
    sid = await _seed_scenario(db, "kw_create")
    new_id = await kw_data.create(
        keyword="ОЧИЩЕНИЕ", match_type="contains", context="comment",
        scenario_id=sid, priority=50,
    )
    rows = await kw_data.list_all()
    target = next(r for r in rows if r["id"] == new_id)
    assert target["keyword"] == "ОЧИЩЕНИЕ"
    assert target["scenario_name"] == "kw_create"


@pytest.mark.asyncio
async def test_update(db) -> None:
    sid = await _seed_scenario(db, "kw_update")
    new_id = await kw_data.create(
        keyword="X", match_type="exact", context="dm",
        scenario_id=sid, priority=100,
    )
    await kw_data.update(
        new_id,
        keyword="Y", match_type="contains", context="both",
        scenario_id=sid, priority=10, case_sensitive=True, active=False,
    )
    row = await db.fetchrow("SELECT * FROM keywords WHERE id = $1", new_id)
    assert row["keyword"] == "Y"
    assert row["context"] == "both"
    assert row["active"] is False


@pytest.mark.asyncio
async def test_delete(db) -> None:
    sid = await _seed_scenario(db, "kw_delete")
    new_id = await kw_data.create(
        keyword="DEL", match_type="exact", context="dm",
        scenario_id=sid,
    )
    await kw_data.delete(new_id)
    row = await db.fetchrow("SELECT * FROM keywords WHERE id = $1", new_id)
    assert row is None
```

c) Создать `tests/test_admin_audit.py`:

```python
"""Tests for admin audit log."""
from __future__ import annotations

import pytest

from admin.data import audit


@pytest.mark.asyncio
async def test_record_and_recent(db) -> None:
    await audit.record_action(
        actor="yulia",
        action="reply",
        target_type="conversation",
        target_id=42,
        details={"length": 100},
    )
    rows = await audit.recent(limit=10)
    target = next(r for r in rows if r["target_id"] == 42 and r["action"] == "reply")
    assert target["actor"] == "yulia"
    assert target["details"]["length"] == 100
```

d) Создать `tests/test_admin_data_stats.py`:

```python
"""Smoke tests for stats queries — they must not fail on empty DB."""
from __future__ import annotations

import pytest

from admin.data import stats


@pytest.mark.asyncio
async def test_daily_new_leads_empty(db) -> None:
    result = await stats.daily_new_leads(days=14)
    assert isinstance(result, list)


@pytest.mark.asyncio
async def test_conversion_to_telegram_empty(db) -> None:
    result = await stats.conversion_to_telegram(days=30)
    assert "total" in result
    assert "converted" in result
    assert isinstance(result["total"], int)


@pytest.mark.asyncio
async def test_handover_breakdown_empty(db) -> None:
    assert await stats.handover_breakdown(days=30) == []


@pytest.mark.asyncio
async def test_claude_token_usage_empty(db) -> None:
    assert await stats.claude_token_usage(days=7) == []
```

### 11. Makefile

a) Добавить в `Makefile` цель для локальной отладки админки без docker:

```makefile
admin-local:
	uv run streamlit run admin/streamlit_app.py --server.port 8501

admin-logs:
	docker compose logs -f admin
```

   `admin-local` удобно когда быстро итерируешь UI без перезапуска docker.

---

## Acceptance criteria

- [ ] Файлы созданы по структуре подзадач 1–9
- [ ] Миграция 012 применена: `\dt` показывает `admin_audit_log`
- [ ] `make lint` проходит
- [ ] `make test` проходит, все новые тесты зелёные:
  - `test_admin_data_conversations.py` — 4 теста
  - `test_admin_data_keywords.py` — 3 теста
  - `test_admin_audit.py` — 1 тест
  - `test_admin_data_stats.py` — 4 теста
  - Все существующие тесты Tasks 01, 03, 04, 06, 07, 08, 09, 11, 13, 14 продолжают работать
- [ ] `docker compose up -d --build admin` поднимает контейнер
- [ ] `curl http://localhost:8501/_stcore/health` возвращает 200 (Streamlit healthcheck)
- [ ] В браузере на `http://localhost:8501`:
  - Появляется форма входа
  - С неверными credentials остаётся на форме входа
  - С верными — попадаешь во «Входящие»
  - Сайдбар содержит 5 разделов
  - На «Сценарии» видны default_welcome / default_purify_comment / default_smart / default_handover
  - На «Ключевые слова» видны минимум «очищение» + «оператор» + «администратор»
- [ ] При создании нового keyword через UI — он сразу появляется в списке + в `admin_audit_log` есть запись `keyword_create`
- [ ] При нажатии «Закрыть handover» в диалоге со статусом handover_pending — статус меняется на handover_done + audit-запись `close_handover`

---

## Do NOT

- НЕ хранить пароль администратора в БД. Только `.env`. Когда нужна будет ротация — меняется в env + перезапуск контейнера.
- НЕ давать админке прямой доступ на чтение `.env` файлов через UI (дамп секретов). Все настройки только через переменные окружения.
- НЕ позволять админу редактировать `messages` или `social_users.external_id`. Это аудит-данные, immutable.
- НЕ делать массовую отправку (broadcast) в этой задаче. Слишком опасно для MVP без отдельной валидации.
- НЕ открывать админку наружу (publicly) без HTTPS и rate-limit. Локально на 8501 — ок, в проде только через Traefik с auth (Task 17).
- НЕ использовать `st.experimental_*` API — нестабильны между версиями Streamlit.
- НЕ делать админ-страницы в основном FastAPI-приложении. Streamlit отдельным процессом.
- НЕ записывать в `admin_audit_log.details` сырое содержимое сообщений пользователей. Только метаданные (длина, флаги). PII в audit-логе — анти-паттерн compliance.
- НЕ передавать сессию авторизации между запросами через cookies. Используем `st.session_state` — это in-memory per-tab.

---

## Зависимости задачи

- Tasks 01, 03, 04, 06, 07, 08, 09, 11, 13, 14 применены
- В `.env` обязательно установлены `ADMIN_BASIC_AUTH_USER` и `ADMIN_BASIC_AUTH_PASSWORD`
- Не требует Task 05 (SendPulseProvider) — отправка ответа Юли через провайдер пройдёт через `NotImplementedError` пока Task 05 не сделана; тестов это не ломает (тесты не вызывают реальную отправку)

---

## Что после этой задачи

После применения Task 15 у Юли есть автономия:

```
✅ Видит handover-диалоги в реальном времени
✅ Может ответить пользователю прямо из админки
✅ Управляет шаблонами сценариев без релиза
✅ Добавляет/выключает keywords за минуту
✅ Видит метрики: лиды/день, conversion в Telegram, расход Claude
✅ Отключает AI для конкретного пользователя при необходимости
✅ Все действия пишутся в audit log
```

Дальше:

- **Task 05** — SendPulseProvider implementation (нужны Юлины credentials)
- **Task 16** — Monitoring (Sentry, healthcheck endpoints, Telegram alerts при ошибках)
- **Task 17** — Production deployment (VPS, Traefik с HTTPS, доступ к админке через `inbox-admin.<domain>`)
- **Task 18** — Smoke tests + go-live checklist

После Tasks 05 + 17 — система **развёрнута**. После Tasks 16 + 18 — **наблюдаемая** и **прошедшая go-live verification**.

---

**Дата создания:** 2026-05-08
**Применять в:** `D:\Work\social_inbox` после Tasks 01, 03, 04, 06, 07, 08, 09, 11, 13, 14
**Эстимейт:** 6–8 часов на Claude Code + ручная проверка
