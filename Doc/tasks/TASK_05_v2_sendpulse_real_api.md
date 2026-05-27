# Task 05 v2: SendPulseProvider rewrite for real API

> Применить в `D:\Work\social_inbox` **поверх Task 05 v1**. Положить файл в корень проекта, открыть в VS Code, попросить Claude Code: «Прочитай TASK_05_v2_sendpulse_real_api.md и выполни все подзадачи последовательно. Перед каждой подзадачей кратко опиши план, после — что фактически сделано».

---

## Контекст

Task 05 v1 был написан до получения реальных credentials Юли. Я угадал имена endpoint'ов SendPulse Instagram API. После проверки против live API оказалось:

| Что было в Task 05 v1 | Что есть в реальности |
|---|---|
| `GET /instagram/messages` | **Не существует** |
| `GET /instagram/comments` | **Не существует — comments через API SendPulse не отдаёт** |
| `POST /instagram/contacts/send` | ✅ Существует |

Реальные endpoints из официальной спецификации `https://sendpulse.com/swagger/instagram/`:
- `GET /chats` — список чатов с подписчиками + последнее сообщение в каждом
- `GET /chats/messages?contact_id=X` — детали переписки конкретного контакта
- `POST /contacts/send` — отправка сообщения ✅ (без изменений)
- `GET /contacts/get?id=X` — данные контакта
- `GET /bots` — список ботов

**Comments-acquisition недоступен через REST API на любом тарифе.** SendPulse не отдаёт comments из IG постов через API — только через свой внутренний Flow Builder UI.

Это меняет архитектуру для **acquisition layer** (как лиды попадают к нам):
- **Старая модель (v1):** Comment с keyword «ОЧИЩЕНИЕ» → наш бэкенд через webhook/polling → comment-to-DM scenario отправляет DM
- **Новая модель (v2):** Comment с keyword «ОЧИЩЕНИЕ» → **SendPulse Flow Builder** (внутри SendPulse UI) отправляет DM → пользователь отвечает в DM → наш бэкенд через polling `/chats` подхватывает диалог → Claude / scenarios / safety

DM-acquisition (welcome) и smart replies на дальнейшие сообщения работают через наш бэкенд как и раньше — просто источник событий теперь правильный.

---

## Цель

После выполнения этой задачи:

- `app/providers/sendpulse_client.py` переписан под реальный API (методы `list_chats`, `list_chat_messages`, `send_message`, `get_contact`)
- `app/providers/sendpulse.py` переписан: `poll_new_events()` идёт в `/chats`, парсит реальную структуру, корректно фильтрует echo + direction
- `app/providers/sendpulse_cursor.py` упрощён: один глобальный курсор по времени, idempotency через events_log UNIQUE
- Поддержка типов сообщений `text` + `reply_to_story`; остальные skip с логированием
- Пагинация `/chats` через `links.next` (защита от больших объёмов)
- Тесты с фикстурами **из реальных ответов** SendPulse API (твоего вывода)
- E2E тест через mocked SendPulse: webhook → polling → events_log → worker → scenario → send
- Документация для Юли: `docs/SendPulse_Flow_Setup.docx` — как настроить comment-to-DM acquisition внутри SendPulse Flow Builder
- В CLAUDE.md обновлён § 7.2 — описание hybrid acquisition

---

## Подзадачи

### 1. Очистка от мёртвого кода v1

a) Удалить старый `_poll_messages` и `_poll_comments` методы из `app/providers/sendpulse.py` (полная замена кода в подзадаче 3).

b) Старый `parse_webhook` оставить как есть — он не использовался в polling-режиме и при апгрейде до paid пригодится. Но обновить comment в docstring: «При апгрейде до paid тарифа SendPulse — payload приходит в том же формате, что описан в `/swagger/instagram/`. Сейчас этот endpoint не используется».

c) Удалить старый `_parse_event_item` и старый `_parse_comment_item` — заменим в подзадаче 3.

### 2. Переписать SendPulseClient под реальный API

a) Полностью заменить содержимое `app/providers/sendpulse_client.py`:

```python
"""SendPulse REST API client.

Wraps the SendPulse Instagram Chatbot API. Endpoints match the official
OpenAPI spec at https://sendpulse.com/swagger/instagram/.

Responsibilities:
- OAuth2 client_credentials flow with token caching in Redis
- HTTP retries on transient errors (5xx, 429)
- Graceful 401 handling (refresh token and retry once)
- Typed methods for the endpoints we actually use: list_chats,
  list_chat_messages, send_message, get_contact

Does NOT contain provider-level logic (event parsing, cursor management).
That stays in app/providers/sendpulse.py.
"""
from __future__ import annotations

import asyncio
from typing import Any

import httpx

from app.repos.redis_client import get_redis
from app.utils.logging import get_logger

log = get_logger(__name__)

TOKEN_REDIS_KEY = "sendpulse:access_token"
TOKEN_TTL_SECONDS = 50 * 60


class SendPulseAuthError(Exception):
    """OAuth or token refresh failed."""


class SendPulseAPIError(Exception):
    """Non-2xx response after retries."""
    def __init__(self, status: int, body: str, *, path: str = "") -> None:
        self.status = status
        self.body = body
        self.path = path
        super().__init__(f"SendPulse API error {status} on {path}: {body[:200]}")


class SendPulseClient:
    """Async HTTP client for SendPulse Instagram API."""

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        base_url: str = "https://api.sendpulse.com",
        timeout: float = 15.0,
    ) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._base_url = base_url.rstrip("/")
        self._timeout = httpx.Timeout(timeout)

    # ---- Auth (unchanged from v1) ----

    async def _fetch_new_token(self) -> str:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(
                f"{self._base_url}/oauth/access_token",
                json={
                    "grant_type": "client_credentials",
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                },
            )
        if response.status_code != 200:
            raise SendPulseAuthError(
                f"OAuth failed: status={response.status_code}, body={response.text[:300]}"
            )
        data = response.json()
        token = data.get("access_token")
        if not token:
            raise SendPulseAuthError(f"OAuth response missing access_token: {data}")
        return token

    async def _get_token(self, force_refresh: bool = False) -> str:
        redis = await get_redis()
        if not force_refresh:
            cached = await redis.get(TOKEN_REDIS_KEY)
            if cached:
                return cached
        token = await self._fetch_new_token()
        await redis.set(TOKEN_REDIS_KEY, token, ex=TOKEN_TTL_SECONDS)
        log.info("sendpulse_token_refreshed")
        return token

    # ---- Generic request wrapper ----

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        max_retries: int = 3,
        absolute_url: str | None = None,
    ) -> dict[str, Any]:
        """Perform an authenticated API request with retries.

        Either `path` (relative) or `absolute_url` (full URL, used for pagination
        via `links.next`) must be provided.
        """
        url = absolute_url or f"{self._base_url}{path}"

        for attempt in range(max_retries + 1):
            token = await self._get_token()
            headers = {"Authorization": f"Bearer {token}"}

            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.request(
                    method, url,
                    params=params,
                    json=json,
                    headers=headers,
                )

            if 200 <= response.status_code < 300:
                if not response.content:
                    return {}
                return response.json()

            # 401 — refresh once
            if response.status_code == 401 and attempt == 0:
                log.warning("sendpulse_401_refreshing_token", path=path)
                await self._get_token(force_refresh=True)
                continue

            # 403 — never retry (likely plan limit)
            if response.status_code == 403:
                raise SendPulseAPIError(403, response.text, path=path)

            # 5xx / 429 — backoff
            if response.status_code in (429, 500, 502, 503, 504) and attempt < max_retries:
                backoff = 2 ** attempt
                log.warning(
                    "sendpulse_transient_error_retrying",
                    status=response.status_code,
                    path=path,
                    attempt=attempt + 1,
                    backoff=backoff,
                )
                await asyncio.sleep(backoff)
                continue

            raise SendPulseAPIError(response.status_code, response.text, path=path)

        raise SendPulseAPIError(0, f"exhausted {max_retries} retries", path=path)

    # ---- Typed endpoints ----

    async def list_chats(
        self,
        bot_id: str,
        *,
        size: int = 50,
        skip: int = 0,
    ) -> dict[str, Any]:
        """GET /instagram/chats — list chats with subscribers.

        Returns the raw JSON response with shape:
            {
              "success": true,
              "data": [
                {
                  "inbox_last_message": {...Message},
                  "inbox_unread": int,
                  "contact": {...Contact}
                },
                ...
              ],
              "links": {"next": "https://api.sendpulse.com/api/instagram/chats?..."},
              "meta": {"total": int, "limit": int}
            }
        """
        return await self.request(
            "GET", "/instagram/chats",
            params={"bot_id": bot_id, "size": size, "skip": skip},
        )

    async def list_chats_next(self, next_url: str) -> dict[str, Any]:
        """Continue pagination using a links.next URL from a previous response."""
        return await self.request("GET", "", absolute_url=next_url)

    async def list_chat_messages(
        self,
        contact_id: str,
        *,
        size: int = 20,
        order: str = "desc",
    ) -> dict[str, Any]:
        """GET /instagram/chats/messages — get messages for a specific contact.

        Used for deep context fetch (e.g. when Claude needs more history than
        what's in our DB). Not used in polling — polling reads only the latest
        message from /chats.
        """
        return await self.request(
            "GET", "/instagram/chats/messages",
            params={"contact_id": contact_id, "size": size, "order": order},
        )

    async def send_message(
        self,
        contact_id: str,
        messages: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """POST /instagram/contacts/send — send DM to a contact.

        `messages` is a list of message blocks per SendPulse spec, e.g.:
            [{"type": "text", "message": {"text": "Hi!"}}]
            [{"type": "image", "message": {"attachment": {...}}}]
        """
        return await self.request(
            "POST", "/instagram/contacts/send",
            json={"contact_id": contact_id, "messages": messages},
        )

    async def get_contact(self, contact_id: str) -> dict[str, Any]:
        """GET /instagram/contacts/get — get contact info by ID."""
        return await self.request(
            "GET", "/instagram/contacts/get",
            params={"id": contact_id},
        )
```

### 3. Переписать SendPulseProvider

a) Полностью заменить содержимое `app/providers/sendpulse.py`:

```python
"""SendPulse messaging provider — polling-based for Free tier.

API discovery: https://sendpulse.com/swagger/instagram/

Polling flow:
1. arq cron job runs `sendpulse_poll_tick` every N seconds
2. provider.poll_new_events() calls GET /instagram/chats with pagination
3. For each chat with a NEW incoming message (created_at > cursor), build
   an IncomingEvent
4. Filter out: echo messages (is_echo=true), outgoing (direction=2),
   unsupported types
5. Cursor advanced to the max created_at seen

Send flow:
1. provider.send(msg) called by worker after scenario engine produces a reply
2. POST /instagram/contacts/send with the configured contact_id
3. SendPulse responds with {"success": true, "data": {...sent message...}}

Comment-to-DM acquisition is handled INSIDE SendPulse Flow Builder
(not in this code). See docs/SendPulse_Flow_Setup.docx.

The webhook endpoint (parse_webhook) is kept intact so that upgrading to
a paid plan = just flip SENDPULSE_POLLING_ENABLED=false.
"""
from __future__ import annotations

import hashlib
import hmac
import json
from datetime import UTC, datetime
from typing import Any, ClassVar

from app.config import get_settings
from app.models.enums import Platform, ProviderName
from app.models.events import IncomingEvent, OutgoingMessage
from app.providers.base import MessagingProvider
from app.providers.sendpulse_client import (
    SendPulseAPIError,
    SendPulseAuthError,
    SendPulseClient,
)
from app.providers.sendpulse_cursor import get_cursor, set_cursor
from app.utils.logging import get_logger

log = get_logger(__name__)

# Message types we know how to convert into an IncomingEvent.
# "reply_to_story" treated as a regular text message (data.text contains the reaction).
SUPPORTED_TYPES = {"text", "reply_to_story"}

# Hard cap on /chats pages we follow in one polling tick.
# Prevents infinite loops on misbehaving API responses.
MAX_PAGES_PER_TICK = 5


class SendPulseProvider(MessagingProvider):
    """SendPulse Instagram chatbot provider (polling-based for Free tier)."""

    name: ClassVar[ProviderName] = "sendpulse"

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        webhook_secret: str,
    ) -> None:
        self._client = SendPulseClient(client_id, client_secret)
        self._webhook_secret = webhook_secret

    # ─────────────────────── Webhook (paid-tier ready) ───────────────────────

    async def parse_webhook(
        self,
        raw_body: bytes,
        headers: dict[str, str],
    ) -> list[IncomingEvent]:
        """Parse webhook payload from SendPulse.

        Active only when on a paid plan with webhook configured in SendPulse UI.
        On Free plan + polling, this endpoint is never hit by SendPulse — returns
        empty list defensively.

        Signature: HMAC-SHA256 of raw_body using webhook_secret, in X-Signature header.
        """
        if not self._webhook_secret:
            log.info("sendpulse_webhook_no_secret_configured_polling_mode")
            return []

        signature = headers.get("x-signature") or headers.get("X-Signature")
        if not signature:
            log.warning("sendpulse_webhook_missing_signature")
            return []

        expected = hmac.new(
            self._webhook_secret.encode("utf-8"),
            raw_body, hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(expected, signature):
            log.warning("sendpulse_webhook_invalid_signature")
            return []

        try:
            payload = json.loads(raw_body)
        except json.JSONDecodeError:
            log.warning("sendpulse_webhook_invalid_json")
            return []

        # Webhook payload shape mirrors what /chats returns per item.
        # Treating each event as a {contact: ..., inbox_last_message: ...} pair.
        events_raw = payload if isinstance(payload, list) else payload.get("data", [])
        events: list[IncomingEvent] = []
        for raw in events_raw:
            parsed = self._parse_chat_item(raw)
            if parsed:
                events.append(parsed)
        return events

    # ─────────────────────────── Polling ────────────────────────────

    async def poll_new_events(self) -> list[IncomingEvent]:
        """Fetch new chat messages since the last polling cursor.

        Strategy:
        - Walk /chats pages until either:
          (a) we've collected all chats updated since cursor, OR
          (b) we hit MAX_PAGES_PER_TICK, OR
          (c) we encounter a chat whose last_message.created_at is <= cursor
              (chats are sorted by last_activity_at DESC, so once we see an old
              one, all subsequent ones are older too — early exit optimization)
        - For each chat: if last message is fresh AND incoming AND supported type,
          convert to IncomingEvent.
        - Idempotency: external_event_id == SendPulse message id; events_log
          UNIQUE INDEX on (provider_name, external_event_id) drops dupes from
          overlapping polling windows.
        """
        settings = get_settings()
        bot_id = settings.sendpulse_bot_id
        if not bot_id:
            log.warning("sendpulse_polling_no_bot_id")
            return []

        since = await get_cursor(bot_id)
        log.info("sendpulse_polling_start", since=since.isoformat())

        events: list[IncomingEvent] = []
        latest_ts = since
        next_url: str | None = None
        page = 0

        try:
            while page < MAX_PAGES_PER_TICK:
                page += 1
                if next_url:
                    response = await self._client.list_chats_next(next_url)
                else:
                    response = await self._client.list_chats(bot_id, size=50)

                chats = response.get("data") or []
                if not chats:
                    break

                page_events, page_latest, hit_old = self._process_chats_page(
                    chats, cursor=since,
                )
                events.extend(page_events)
                if page_latest > latest_ts:
                    latest_ts = page_latest

                # Early exit: chats sorted by last_activity DESC. If we found a
                # chat older than cursor, everything after is also older.
                if hit_old:
                    log.debug("sendpulse_polling_early_exit", page=page)
                    break

                # Pagination
                links = response.get("links") or {}
                next_url = links.get("next")
                if not next_url:
                    break

        except SendPulseAuthError as exc:
            log.error("sendpulse_polling_auth_failed", error=str(exc))
            return []
        except SendPulseAPIError as exc:
            log.warning(
                "sendpulse_polling_api_error",
                status=exc.status, path=exc.path, body=exc.body[:200],
            )
            return []

        await set_cursor(bot_id, latest_ts)
        log.info(
            "sendpulse_polling_done",
            events_count=len(events),
            pages_fetched=page,
            new_cursor=latest_ts.isoformat(),
        )
        return events

    def _process_chats_page(
        self,
        chats: list[dict[str, Any]],
        *,
        cursor: datetime,
    ) -> tuple[list[IncomingEvent], datetime, bool]:
        """Process one page of /chats response.

        Returns (events, latest_ts_seen, hit_old_chat).
        """
        events: list[IncomingEvent] = []
        latest_ts = cursor
        hit_old = False

        for chat in chats:
            last_msg = chat.get("inbox_last_message") or {}
            msg_ts = _parse_ts(last_msg.get("created_at"))

            # Early-exit signal: we're past the cursor in chronologically-sorted list
            if msg_ts <= cursor:
                hit_old = True
                continue

            if msg_ts > latest_ts:
                latest_ts = msg_ts

            event = self._parse_chat_item(chat)
            if event:
                events.append(event)

        return events, latest_ts, hit_old

    # ──────────────────────────── Send ────────────────────────────

    async def send(self, msg: OutgoingMessage) -> str | None:
        """Send a DM via SendPulse.

        SendPulse API has only one outbound endpoint — /contacts/send. There's
        no separate 'private reply to comment' on Free tier (and on paid, it
        goes through Flow Builder, not REST). So reply_to_comment_id is ignored;
        the bot's first DM acts as the private reply.
        """
        try:
            block = self._build_message_block(msg)
            response = await self._client.send_message(msg.external_user_id, [block])
        except SendPulseAuthError as exc:
            log.error("sendpulse_send_auth_failed", error=str(exc))
            return None
        except SendPulseAPIError as exc:
            log.warning(
                "sendpulse_send_api_error",
                status=exc.status, body=exc.body[:200],
                contact_id=msg.external_user_id,
            )
            return None

        # Response shape (per spec): {"success": true, "data": {...} | [{...}]}
        # `data` can be either a single object or an array. Extract first id we find.
        data = response.get("data")
        message_id: str | None = None
        if isinstance(data, list) and data:
            message_id = str(data[0].get("id") or data[0].get("message_id") or "") or None
        elif isinstance(data, dict):
            message_id = str(data.get("id") or data.get("message_id") or "") or None

        if not message_id:
            log.warning(
                "sendpulse_send_no_message_id",
                response_keys=list(response.keys()),
            )
        return message_id

    def _build_message_block(self, msg: OutgoingMessage) -> dict[str, Any]:
        """Build SendPulse outbound message block from OutgoingMessage.

        Supports text (default) and quick-reply buttons (URL or postback).
        Media URLs not supported in this version.
        """
        if msg.quick_replies:
            # Use generic_template with buttons (per SendPulse spec example)
            buttons = []
            for qr in msg.quick_replies:
                if qr.payload.startswith(("http://", "https://")):
                    buttons.append({
                        "type": "web_url",
                        "title": qr.title,
                        "url": qr.payload,
                    })
                else:
                    buttons.append({
                        "type": "postback",
                        "title": qr.title,
                        "data": {"text": qr.payload},
                    })
            return {
                "type": "generic_template",
                "message": {
                    "attachment": {
                        "payload": {
                            "elements": [{
                                "title": msg.text or " ",
                                "buttons": buttons,
                            }]
                        }
                    }
                },
            }

        return {"type": "text", "message": {"text": msg.text or ""}}

    # ────────────────────────── Profile ───────────────────────────

    async def fetch_user_profile(
        self,
        platform: Platform,
        external_user_id: str,
    ) -> dict[str, Any]:
        """Return contact info from SendPulse.

        Polling normally pre-populates this data into IncomingEvent already,
        so this is rarely called. Returns empty dict on failure.
        """
        try:
            response = await self._client.get_contact(external_user_id)
        except SendPulseAPIError as exc:
            log.warning(
                "sendpulse_profile_failed",
                contact_id=external_user_id, status=exc.status,
            )
            return {}

        data = response.get("data") or {}
        if not isinstance(data, dict):
            return {}
        channel = data.get("channel_data") or {}
        return {
            "username": channel.get("user_name"),
            "full_name": channel.get("name"),
            "profile_pic_url": channel.get("profile_pic"),
        }

    # ───────────────────────── Parsing ────────────────────────────

    def _parse_chat_item(self, chat: dict[str, Any]) -> IncomingEvent | None:
        """Convert one /chats response item into an IncomingEvent.

        Skip rules (return None):
        - missing inbox_last_message
        - direction != 1 (not incoming)
        - is_echo == True (our own outgoing returned by Meta echo)
        - type not in SUPPORTED_TYPES
        - missing required fields (contact_id, message id)
        """
        last_msg = chat.get("inbox_last_message")
        if not isinstance(last_msg, dict):
            return None

        # Direction filter
        if last_msg.get("direction") != 1:
            return None

        # Echo filter
        data = last_msg.get("data") or {}
        if data.get("is_echo") is True:
            return None

        # Type filter
        msg_type = last_msg.get("type")
        if msg_type not in SUPPORTED_TYPES:
            log.debug(
                "sendpulse_skipping_unsupported_type",
                msg_type=msg_type,
                message_id=last_msg.get("id"),
            )
            return None

        # Extract text
        text = data.get("text")
        if not text:
            log.debug("sendpulse_skipping_no_text", message_id=last_msg.get("id"))
            return None

        # IDs
        contact = chat.get("contact") or {}
        contact_id = last_msg.get("contact_id") or contact.get("id")
        if not contact_id:
            log.warning("sendpulse_missing_contact_id", message_id=last_msg.get("id"))
            return None

        message_id = last_msg.get("id")
        if not message_id:
            log.warning("sendpulse_missing_message_id", contact_id=contact_id)
            return None

        # Profile from channel_data
        channel = contact.get("channel_data") or {}
        username = channel.get("user_name")
        full_name = channel.get("name") or channel.get("first_name")

        return IncomingEvent(
            provider="sendpulse",
            platform="instagram",
            event_type="message",
            external_user_id=str(contact_id),
            external_event_id=str(message_id),
            username=username,
            full_name=full_name,
            text=text,
            occurred_at=_parse_ts(last_msg.get("created_at")),
            raw_payload=chat,  # store the WHOLE chat item for audit
        )


def _parse_ts(value: Any) -> datetime:
    """Parse SendPulse timestamp (ISO string or unix epoch) → aware UTC datetime."""
    if value is None:
        return datetime.now(UTC)
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=UTC)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            pass
    return datetime.now(UTC)
```

### 4. Cursor — без изменений

Файл `app/providers/sendpulse_cursor.py` остаётся как был в Task 05 v1. Логика та же: глобальный курсор по времени для бота, начало с `NOW()-5min` если нет cursor.

Только в Task 05 v1 был cursor `NOW()-5min` — это всё ещё ок. Но в боевом запуске на live аккаунте Юли с 95 непрочитанными сообщениями мы получим **разовый bulk processing** этих 95 при первом polling-tick. Это нормально, но Claude может быстро выбрать daily budget. Решение в **подзадаче 5**.

### 5. Защита от bulk processing на холодном старте

a) В `app/providers/sendpulse_cursor.py` обновить значение по умолчанию для первого запуска:

   Найти:
```python
    return datetime.now(UTC) - timedelta(minutes=5)
```

   Заменить на:
```python
    # On fresh deployment, start "from now" — don't backfill 95 unread messages.
    # Operator can manually trigger backfill via admin tool if needed.
    return datetime.now(UTC)
```

b) Зафиксировать решение в комментарии к функции:

```python
async def get_cursor(bot_id: str) -> datetime:
    """Return last polled timestamp, or NOW() if absent.

    Design choice: on first deployment we DO NOT backfill history. This avoids
    a thundering herd of Claude calls when activating the bot against an
    account that already has 95+ unread DMs (real case from Yulia's account
    on 2026-05-26). Operator can manually backfill by:
    1. Clearing the cursor: `redis-cli DEL sendpulse:cursor:<bot_id>`
    2. Setting cursor to a past datetime, e.g.:
       `redis-cli SET sendpulse:cursor:<bot_id> "2026-05-01T00:00:00+00:00"`
    """
```

### 6. Обновить конфиг

a) В `app/config.py` поле `sendpulse_polling_interval_seconds` оставить без изменений (30 сек по умолчанию).

b) Никаких новых полей конфигурации не требуется.

### 7. Тесты

a) Полностью переписать `tests/test_sendpulse_client.py` под новые методы. Старые тесты на `/instagram/messages` и `/instagram/comments` удалить.

```python
"""Tests for SendPulseClient — real API endpoints."""
from __future__ import annotations

import pytest
from pytest_httpx import HTTPXMock

from app.providers.sendpulse_client import (
    SendPulseAPIError,
    SendPulseAuthError,
    SendPulseClient,
)
from app.repos.redis_client import get_redis


@pytest.fixture(autouse=True)
async def _clear_token_cache() -> None:
    redis = await get_redis()
    await redis.delete("sendpulse:access_token")
    yield
    await redis.delete("sendpulse:access_token")


@pytest.mark.asyncio
async def test_oauth_fetches_token(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        method="POST",
        url="https://api.sendpulse.com/oauth/access_token",
        json={"access_token": "test_token", "expires_in": 3600},
    )
    client = SendPulseClient("cid", "csecret")
    token = await client._get_token()
    assert token == "test_token"


@pytest.mark.asyncio
async def test_list_chats_returns_data(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        method="POST",
        url="https://api.sendpulse.com/oauth/access_token",
        json={"access_token": "tok"},
    )
    httpx_mock.add_response(
        method="GET",
        url__startswith="https://api.sendpulse.com/instagram/chats",
        json={
            "success": True,
            "data": [{"inbox_last_message": {"id": "msg1"}, "contact": {}}],
            "meta": {"total": 1, "limit": 50},
        },
    )
    client = SendPulseClient("cid", "csecret")
    result = await client.list_chats("bot_xyz")
    assert result["success"] is True
    assert len(result["data"]) == 1
    assert result["data"][0]["inbox_last_message"]["id"] == "msg1"


@pytest.mark.asyncio
async def test_list_chats_next_uses_absolute_url(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        method="POST",
        url="https://api.sendpulse.com/oauth/access_token",
        json={"access_token": "tok"},
    )
    next_url = "https://api.sendpulse.com/api/instagram/chats?bot_id=bot_xyz&page=2&jwt=..."
    httpx_mock.add_response(
        method="GET", url=next_url,
        json={"success": True, "data": []},
    )
    client = SendPulseClient("cid", "csecret")
    result = await client.list_chats_next(next_url)
    assert result["success"] is True


@pytest.mark.asyncio
async def test_send_message_posts_correct_body(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        method="POST",
        url="https://api.sendpulse.com/oauth/access_token",
        json={"access_token": "tok"},
    )
    httpx_mock.add_response(
        method="POST",
        url="https://api.sendpulse.com/instagram/contacts/send",
        json={"success": True, "data": {"id": "sent_msg_1"}},
    )
    client = SendPulseClient("cid", "csecret")
    result = await client.send_message(
        "contact_xyz",
        [{"type": "text", "message": {"text": "Hi!"}}],
    )
    assert result["data"]["id"] == "sent_msg_1"

    # Verify request body
    import json as _json
    sent = httpx_mock.get_requests(url="https://api.sendpulse.com/instagram/contacts/send")[0]
    body = _json.loads(sent.content)
    assert body == {
        "contact_id": "contact_xyz",
        "messages": [{"type": "text", "message": {"text": "Hi!"}}],
    }


@pytest.mark.asyncio
async def test_403_not_retried(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        method="POST",
        url="https://api.sendpulse.com/oauth/access_token",
        json={"access_token": "tok"},
    )
    httpx_mock.add_response(
        method="GET",
        url__startswith="https://api.sendpulse.com/instagram/chats",
        status_code=403, text="paid feature",
    )
    client = SendPulseClient("cid", "csecret")
    with pytest.raises(SendPulseAPIError) as exc:
        await client.list_chats("bot_xyz")
    assert exc.value.status == 403


@pytest.mark.asyncio
async def test_401_refreshes_token_once(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        method="POST", url="https://api.sendpulse.com/oauth/access_token",
        json={"access_token": "first_token"},
    )
    httpx_mock.add_response(
        method="GET",
        url__startswith="https://api.sendpulse.com/instagram/chats",
        status_code=401,
    )
    httpx_mock.add_response(
        method="POST", url="https://api.sendpulse.com/oauth/access_token",
        json={"access_token": "refreshed_token"},
    )
    httpx_mock.add_response(
        method="GET",
        url__startswith="https://api.sendpulse.com/instagram/chats",
        json={"success": True, "data": []},
    )
    client = SendPulseClient("cid", "csecret")
    result = await client.list_chats("bot_xyz")
    assert result["success"] is True
```

b) Полностью переписать `tests/test_sendpulse_provider.py` с фикстурами из реальных данных:

```python
"""Tests for SendPulseProvider — parsing real /chats response items."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pytest_httpx import HTTPXMock

from app.models.events import OutgoingMessage, QuickReply
from app.providers.sendpulse import SendPulseProvider
from app.providers.sendpulse_cursor import set_cursor
from app.repos.redis_client import get_redis


# ─── Fixtures: real shapes from Yulia's account ───

CHAT_INCOMING_TEXT = {
    "inbox_last_message": {
        "contact_id": "6a14d242a0cb77b3d00abf18",
        "bot_id": "6a0b5c1e35f395f025034fb6",
        "type": "text",
        "direction": 1,
        "created_at": "2026-05-25T22:50:43+00:00",
        "id": "6a14d243bea27c81cb0a7492",
        "data": {
            "text": "Здравствуйте! Какие масла посоветуете от гриппа и простуды?",
            "is_echo": False,
            "is_deleted": False,
        },
    },
    "inbox_unread": 1,
    "contact": {
        "id": "6a14d242a0cb77b3d00abf18",
        "bot_id": "6a0b5c1e35f395f025034fb6",
        "channel_data": {
            "id": 4280767578903322,
            "name": "Svetlana",
            "user_name": "svetlana_parf_orig",
            "first_name": "Svetlana",
        },
        "last_activity_at": "2026-05-25T22:50:43+00:00",
    },
}

CHAT_REPLY_TO_STORY = {
    "inbox_last_message": {
        "contact_id": "6a0c09208be53535bc0e5514",
        "type": "reply_to_story",
        "direction": 1,
        "created_at": "2026-05-26T07:12:39+00:00",
        "id": "6a1547e7e69bccac5600817c",
        "data": {
            "text": "🔥",
            "is_echo": False,
            "reply_to": {"story": "story:18..."},
        },
    },
    "inbox_unread": 32,
    "contact": {
        "id": "6a0c09208be53535bc0e5514",
        "channel_data": {
            "name": "анна прощенко",
            "user_name": "proshchenko105",
        },
    },
}

CHAT_OUTGOING = {
    "inbox_last_message": {
        "contact_id": "u1",
        "type": "text",
        "direction": 2,  # outgoing — skip
        "created_at": "2026-05-26T08:00:00+00:00",
        "id": "msg_outgoing",
        "data": {"text": "Our reply", "is_echo": False},
    },
    "contact": {"id": "u1", "channel_data": {"user_name": "x"}},
}

CHAT_ECHO = {
    "inbox_last_message": {
        "contact_id": "u2",
        "type": "text",
        "direction": 1,
        "created_at": "2026-05-26T08:00:00+00:00",
        "id": "msg_echo",
        "data": {"text": "echo", "is_echo": True},
    },
    "contact": {"id": "u2", "channel_data": {"user_name": "x"}},
}

CHAT_UNSUPPORTED_TYPE = {
    "inbox_last_message": {
        "contact_id": "u3",
        "type": "image",
        "direction": 1,
        "created_at": "2026-05-26T08:00:00+00:00",
        "id": "msg_img",
        "data": {"text": None, "is_echo": False},
    },
    "contact": {"id": "u3", "channel_data": {"user_name": "x"}},
}


@pytest.fixture(autouse=True)
async def _clear_keys() -> None:
    redis = await get_redis()
    keys = await redis.keys("sendpulse:*")
    if keys:
        await redis.delete(*keys)
    yield
    keys = await redis.keys("sendpulse:*")
    if keys:
        await redis.delete(*keys)


def _make_provider() -> SendPulseProvider:
    return SendPulseProvider("cid", "csecret", webhook_secret="wsecret")


# ────────── Parsing ──────────

def test_parse_chat_text_message() -> None:
    p = _make_provider()
    event = p._parse_chat_item(CHAT_INCOMING_TEXT)
    assert event is not None
    assert event.external_user_id == "6a14d242a0cb77b3d00abf18"
    assert event.external_event_id == "6a14d243bea27c81cb0a7492"
    assert event.username == "svetlana_parf_orig"
    assert event.full_name == "Svetlana"
    assert "грипп" in event.text
    assert event.event_type == "message"


def test_parse_chat_reply_to_story_as_text() -> None:
    p = _make_provider()
    event = p._parse_chat_item(CHAT_REPLY_TO_STORY)
    assert event is not None
    assert event.text == "🔥"
    assert event.username == "proshchenko105"


def test_parse_skips_outgoing() -> None:
    p = _make_provider()
    assert p._parse_chat_item(CHAT_OUTGOING) is None


def test_parse_skips_echo() -> None:
    p = _make_provider()
    assert p._parse_chat_item(CHAT_ECHO) is None


def test_parse_skips_unsupported_type() -> None:
    p = _make_provider()
    assert p._parse_chat_item(CHAT_UNSUPPORTED_TYPE) is None


def test_parse_handles_missing_inbox_last_message() -> None:
    p = _make_provider()
    assert p._parse_chat_item({"contact": {}}) is None


def test_parse_handles_missing_contact_id() -> None:
    p = _make_provider()
    broken = {
        "inbox_last_message": {
            "type": "text", "direction": 1, "id": "x",
            "created_at": "2026-05-26T08:00:00+00:00",
            "data": {"text": "hi", "is_echo": False},
        },
        "contact": {},
    }
    assert p._parse_chat_item(broken) is None


# ────────── Polling ──────────

@pytest.mark.asyncio
async def test_polling_returns_new_events(
    httpx_mock: HTTPXMock, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SENDPULSE_BOT_ID", "bot_test")
    from app.config import get_settings
    get_settings.cache_clear()

    # Set cursor to BEFORE the test messages
    await set_cursor("bot_test", datetime(2026, 5, 25, 0, 0, tzinfo=UTC))

    httpx_mock.add_response(
        method="POST", url="https://api.sendpulse.com/oauth/access_token",
        json={"access_token": "tok"},
    )
    httpx_mock.add_response(
        method="GET",
        url__startswith="https://api.sendpulse.com/instagram/chats",
        json={
            "success": True,
            "data": [CHAT_INCOMING_TEXT, CHAT_REPLY_TO_STORY,
                     CHAT_OUTGOING, CHAT_ECHO, CHAT_UNSUPPORTED_TYPE],
            "meta": {"total": 5, "limit": 50},
        },
    )

    p = _make_provider()
    events = await p.poll_new_events()

    # 2 events parsed: text + reply_to_story; outgoing/echo/unsupported skipped
    assert len(events) == 2
    user_ids = {e.external_user_id for e in events}
    assert user_ids == {
        "6a14d242a0cb77b3d00abf18",   # svetlana
        "6a0c09208be53535bc0e5514",   # anna
    }


@pytest.mark.asyncio
async def test_polling_advances_cursor(
    httpx_mock: HTTPXMock, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SENDPULSE_BOT_ID", "bot_test")
    from app.config import get_settings
    get_settings.cache_clear()

    await set_cursor("bot_test", datetime(2026, 5, 25, 0, 0, tzinfo=UTC))

    httpx_mock.add_response(
        method="POST", url="https://api.sendpulse.com/oauth/access_token",
        json={"access_token": "tok"},
    )
    httpx_mock.add_response(
        method="GET",
        url__startswith="https://api.sendpulse.com/instagram/chats",
        json={
            "success": True,
            "data": [CHAT_INCOMING_TEXT],   # 2026-05-25T22:50:43
        },
    )
    p = _make_provider()
    await p.poll_new_events()

    from app.providers.sendpulse_cursor import get_cursor
    new_cursor = await get_cursor("bot_test")
    # Cursor advanced to the message timestamp
    assert new_cursor == datetime(2026, 5, 25, 22, 50, 43, tzinfo=UTC)


@pytest.mark.asyncio
async def test_polling_skips_old_messages(
    httpx_mock: HTTPXMock, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SENDPULSE_BOT_ID", "bot_test")
    from app.config import get_settings
    get_settings.cache_clear()

    # Cursor AFTER the test message — should skip it
    await set_cursor("bot_test", datetime(2026, 12, 31, tzinfo=UTC))

    httpx_mock.add_response(
        method="POST", url="https://api.sendpulse.com/oauth/access_token",
        json={"access_token": "tok"},
    )
    httpx_mock.add_response(
        method="GET",
        url__startswith="https://api.sendpulse.com/instagram/chats",
        json={"success": True, "data": [CHAT_INCOMING_TEXT]},
    )
    p = _make_provider()
    events = await p.poll_new_events()
    assert events == []


@pytest.mark.asyncio
async def test_polling_follows_pagination(
    httpx_mock: HTTPXMock, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SENDPULSE_BOT_ID", "bot_test")
    from app.config import get_settings
    get_settings.cache_clear()
    await set_cursor("bot_test", datetime(2026, 5, 1, tzinfo=UTC))

    httpx_mock.add_response(
        method="POST", url="https://api.sendpulse.com/oauth/access_token",
        json={"access_token": "tok"},
    )
    # Page 1 with links.next
    httpx_mock.add_response(
        method="GET",
        url__startswith="https://api.sendpulse.com/instagram/chats?bot_id",
        json={
            "success": True,
            "data": [CHAT_INCOMING_TEXT],
            "links": {"next": "https://api.sendpulse.com/api/instagram/chats?page=2&jwt=x"},
        },
    )
    # Page 2 (no further next)
    httpx_mock.add_response(
        method="GET",
        url="https://api.sendpulse.com/api/instagram/chats?page=2&jwt=x",
        json={"success": True, "data": [CHAT_REPLY_TO_STORY]},
    )

    p = _make_provider()
    events = await p.poll_new_events()
    assert len(events) == 2


# ────────── Send ──────────

@pytest.mark.asyncio
async def test_send_text(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        method="POST", url="https://api.sendpulse.com/oauth/access_token",
        json={"access_token": "tok"},
    )
    httpx_mock.add_response(
        method="POST",
        url="https://api.sendpulse.com/instagram/contacts/send",
        json={"success": True, "data": {"id": "out_msg_1"}},
    )
    p = _make_provider()
    result = await p.send(OutgoingMessage(
        platform="instagram",
        external_user_id="contact_x",
        text="Привет!",
    ))
    assert result == "out_msg_1"


@pytest.mark.asyncio
async def test_send_with_url_button(httpx_mock: HTTPXMock) -> None:
    import json as _json
    httpx_mock.add_response(
        method="POST", url="https://api.sendpulse.com/oauth/access_token",
        json={"access_token": "tok"},
    )
    httpx_mock.add_response(
        method="POST",
        url="https://api.sendpulse.com/instagram/contacts/send",
        json={"success": True, "data": {"id": "out_btn_1"}},
    )
    p = _make_provider()
    await p.send(OutgoingMessage(
        platform="instagram",
        external_user_id="contact_x",
        text="Перейди в Telegram",
        quick_replies=[QuickReply(
            title="Перейти",
            payload="https://t.me/yuliya_purify_bot?start=ig_abc12345_purify",
        )],
    ))

    sent = httpx_mock.get_requests(
        url="https://api.sendpulse.com/instagram/contacts/send",
    )[0]
    body = _json.loads(sent.content)
    block = body["messages"][0]
    assert block["type"] == "generic_template"
    elements = block["message"]["attachment"]["payload"]["elements"]
    assert elements[0]["buttons"][0]["type"] == "web_url"
    assert elements[0]["buttons"][0]["url"].startswith("https://t.me/")


@pytest.mark.asyncio
async def test_send_returns_none_on_error(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        method="POST", url="https://api.sendpulse.com/oauth/access_token",
        json={"access_token": "tok"},
    )
    for _ in range(4):
        httpx_mock.add_response(
            method="POST",
            url="https://api.sendpulse.com/instagram/contacts/send",
            status_code=500,
        )
    p = _make_provider()
    result = await p.send(OutgoingMessage(
        platform="instagram", external_user_id="contact_x", text="x",
    ))
    assert result is None
```

c) Создать `tests/test_e2e_sendpulse_polling.py` — финальный e2e тест на реальной структуре:

```python
"""E2E: SendPulse polling → events_log → worker → scenario → send."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pytest_httpx import HTTPXMock

from app.providers import reset_provider
from app.providers.sendpulse_cursor import set_cursor
from app.repos import users
from app.repos.redis_client import get_redis
from app.workers.tasks_messages import process_incoming_event
from app.workers.tasks_sendpulse import sendpulse_poll_tick


@pytest.fixture(autouse=True)
async def _setup(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MESSAGING_PROVIDER", "sendpulse")
    monkeypatch.setenv("SENDPULSE_POLLING_ENABLED", "true")
    monkeypatch.setenv("SENDPULSE_BOT_ID", "bot_e2e")
    monkeypatch.setenv("SENDPULSE_CLIENT_ID", "cid")
    monkeypatch.setenv("SENDPULSE_CLIENT_SECRET", "csecret")
    from app.config import get_settings
    get_settings.cache_clear()
    reset_provider()

    redis = await get_redis()
    keys = await redis.keys("sendpulse:*")
    if keys:
        await redis.delete(*keys)
    yield
    reset_provider()


@pytest.mark.asyncio
async def test_polling_picks_up_real_dm_and_processes_it(
    httpx_mock: HTTPXMock, db,
) -> None:
    """Full pipeline test against real /chats response shape."""
    await set_cursor("bot_e2e", datetime(2026, 5, 1, tzinfo=UTC))

    httpx_mock.add_response(
        method="POST", url="https://api.sendpulse.com/oauth/access_token",
        json={"access_token": "tok"},
    )
    httpx_mock.add_response(
        method="GET",
        url__startswith="https://api.sendpulse.com/instagram/chats",
        json={
            "success": True,
            "data": [{
                "inbox_last_message": {
                    "contact_id": "e2e_contact_1",
                    "bot_id": "bot_e2e",
                    "type": "text",
                    "direction": 1,
                    "created_at": "2026-05-25T22:50:43+00:00",
                    "id": "e2e_msg_1",
                    "data": {
                        "text": "Привет!",
                        "is_echo": False,
                    },
                },
                "inbox_unread": 1,
                "contact": {
                    "id": "e2e_contact_1",
                    "channel_data": {
                        "name": "Test User",
                        "user_name": "test_user_1",
                        "first_name": "Test",
                    },
                },
            }],
            "meta": {"total": 1, "limit": 50},
        },
    )

    # 1. Polling tick
    await sendpulse_poll_tick({})

    # 2. events_log got a row
    log_row = await db.fetchrow(
        "SELECT * FROM events_log WHERE external_event_id = 'e2e_msg_1'",
    )
    assert log_row is not None
    assert log_row["processed_at"] is None  # not yet processed

    # 3. Manually drive worker (in real life arq dequeues this)
    from app.models.events import IncomingEvent
    event = IncomingEvent(
        provider="sendpulse",
        platform="instagram",
        event_type="message",
        external_user_id="e2e_contact_1",
        external_event_id="e2e_msg_1",
        username="test_user_1",
        full_name="Test User",
        text="Привет!",
        occurred_at=datetime(2026, 5, 25, 22, 50, 43, tzinfo=UTC),
        raw_payload={},
    )
    await process_incoming_event({}, event.model_dump(mode="json"), log_row["id"])

    # 4. User created
    user = await users.get_by_external("sendpulse", "instagram", "e2e_contact_1")
    assert user is not None
    assert user["username"] == "test_user_1"


@pytest.mark.asyncio
async def test_polling_idempotent_on_repeat(
    httpx_mock: HTTPXMock, db,
) -> None:
    """Same message ID twice → only one events_log row."""
    await set_cursor("bot_e2e", datetime(2026, 5, 1, tzinfo=UTC))

    same_chat_payload = {
        "success": True,
        "data": [{
            "inbox_last_message": {
                "contact_id": "idem_contact",
                "type": "text",
                "direction": 1,
                "created_at": "2026-05-25T22:00:00+00:00",
                "id": "idem_msg_1",
                "data": {"text": "x", "is_echo": False},
            },
            "contact": {
                "id": "idem_contact",
                "channel_data": {"user_name": "x"},
            },
        }],
    }

    # First poll
    httpx_mock.add_response(
        method="POST", url="https://api.sendpulse.com/oauth/access_token",
        json={"access_token": "tok"},
    )
    httpx_mock.add_response(
        method="GET",
        url__startswith="https://api.sendpulse.com/instagram/chats",
        json=same_chat_payload,
    )
    await sendpulse_poll_tick({})

    # Roll cursor BACK to before the message — simulate broken cursor scenario
    await set_cursor("bot_e2e", datetime(2026, 5, 1, tzinfo=UTC))

    # Second poll — same data
    httpx_mock.add_response(
        method="GET",
        url__startswith="https://api.sendpulse.com/instagram/chats",
        json=same_chat_payload,
    )
    await sendpulse_poll_tick({})

    # Only one row in events_log
    rows = await db.fetch(
        "SELECT * FROM events_log WHERE external_event_id = 'idem_msg_1'",
    )
    assert len(rows) == 1
```

### 8. CLAUDE.md обновление

a) В CLAUDE.md заменить раздел про SendPulse polling. Найти § 7.2 (или похожий) и заменить:

```markdown
### 7.2. SendPulse — polling vs webhook + hybrid acquisition

SendPulse webhooks доступны только в платных тарифах. До апгрейда работаем
на polling-режиме через **реальные endpoint'ы** (а не угаданные в Task 05 v1):

- Worker раз в 30 сек дёргает `GET /instagram/chats?bot_id=X&size=50`
- Для каждого чата читает `inbox_last_message`; фильтрует direction=1 + is_echo=false
- Поддерживаемые типы: text, reply_to_story (остальные skip + log)
- Cursor — глобальный по времени, в Redis (`sendpulse:cursor:<bot_id>`)
- Дедупликация через `events_log.external_event_id` UNIQUE
- На холодном старте cursor = NOW() (без backfill истории — защита от bulk processing)
- Пагинация через `links.next`, max 5 страниц за один tick

**Comments acquisition недоступен через API на любом тарифе SendPulse.**
SendPulse не отдаёт comments из IG-постов через REST. Comments через API
есть только на платном тарифе через webhook от Meta (в обход SendPulse).

**Hybrid acquisition strategy:**
- **Comment-to-DM** обрабатывается ВНУТРИ SendPulse Flow Builder (UI):
  - Trigger по keyword «ОЧИЩЕНИЕ» под Reels-комментариями
  - Flow отправляет DM с deep-link в Telegram
  - Настройка — см. `docs/SendPulse_Flow_Setup.docx` (для Юли)
- **DM-acquisition + smart replies + handover** работают через наш polling

При смене провайдера (Manychat / Meta direct) → SendPulse-specific код
(polling, cursor) удаляется, новый провайдер использует webhook.

См. `app/providers/sendpulse.py:poll_new_events`,
`app/providers/sendpulse_client.py:list_chats`.

OpenAPI спецификация SendPulse: https://sendpulse.com/swagger/instagram/
```

### 9. Документация для Юли — DOCX Flow Setup

a) Создать `docs/SendPulse_Flow_Setup.md` (исходник для DOCX):

   Содержание — пошаговая инструкция на русском:
   - Что мы делаем (acquisition через keyword «ОЧИЩЕНИЕ» в Reels комментариях)
   - Шаг 1: открыть SendPulse → Чат-боты → Instagram-бот
   - Шаг 2: создать Trigger
     - Triggers → New trigger → Type: keyword
     - Name: «Очищение acquisition»
     - Keywords: `ОЧИЩЕНИЕ`, `очищение`, `Очищение` (3 варианта регистра)
     - Keywords search type: **Contains** (содержит)
   - Шаг 3: создать Flow и привязать к Trigger
     - Flows → New flow → Name: «Очищение → DM с deep-link»
     - Первый элемент: Message → Type: Generic template
     - Title: «Привет! 🌿»
     - Subtitle: «Расскажу подробнее про программу «Очищение» в Telegram-боте»
     - Button: type=URL, title=«Перейти в Telegram», url=`https://t.me/yuliya_purify_bot?start=ig_sp_purify`
   - Шаг 4: связать Trigger → Flow
   - Шаг 5: активировать оба (status=Active)
   - Шаг 6: тестирование — попросить кого-то прокомментировать «ОЧИЩЕНИЕ» под Reels
   - Что должно происходить:
     - Подписчик пишет «ОЧИЩЕНИЕ» в комментарии
     - SendPulse автоматически отправляет ему DM с кнопкой
     - Кнопка ведёт в @yuliya_purify_bot с payload `ig_sp_purify`
     - bot_purify видит payload, запускает welcome → квиз
     - После DM — все последующие сообщения подхватывает наш polling

b) Конвертировать в DOCX тем же стилем что Setup_Guide / Go_Live_Checklist (оранжевые заголовки, [!] warning-боксы, [i] info, Arial, пошаговые «Шаг 1/2/3…»).

   Конкретный JavaScript-скрипт сборки DOCX — добавить в `scripts/build_sendpulse_flow_setup.js`. Использовать те же style helpers, что в Go_Live_Checklist (`Title`, `H1`, `H2`, `Step`, `P`, `Bullet`, `Warn`, `Info`, `Ok`, `HR`).

   Сборка:
   ```bash
   node scripts/build_sendpulse_flow_setup.js docs/SendPulse_Flow_Setup.docx
   ```

### 10. Local_Setup_Guide.docx — апдейт

a) Не трогаем существующий файл — но в roadmap отметить, что SendPulse polling теперь работает корректно. В разделе 4 Шаг 7 (проверка polling) логи теперь должны показывать `sendpulse_polling_done events_count=N` без 401/404 ошибок.

---

## Acceptance criteria

- [ ] Файлы переписаны по подзадачам 1–6
- [ ] `make lint` проходит
- [ ] `make test` проходит, **все** тесты зелёные:
  - `test_sendpulse_client.py` — 6 тестов
  - `test_sendpulse_provider.py` — 12 тестов
  - `test_e2e_sendpulse_polling.py` — 2 ключевых теста
  - Все остальные тесты проекта продолжают работать
- [ ] После `docker compose down && docker compose up -d --force-recreate`:
  ```bash
  docker compose logs worker --tail 50 | findstr sendpulse
  ```
  показывает зелёные строки без 401/404:
  ```
  sendpulse_polling_start since=...
  sendpulse_polling_done events_count=0 new_cursor=...
  ```
  (events_count=0 — потому что cursor стартует с NOW и история не backfill'ится)
- [ ] При получении нового DM (со второго аккаунта в Instagram):
  - В логах: `sendpulse_polling_done events_count=1`
  - В БД появляется новый `social_users` + `messages` запись
  - Срабатывает welcome scenario → отправляется DM с deep-link через `/contacts/send`
  - Юля во второй аккаунт получает приветственное сообщение
- [ ] При повторном получении того же DM (cursor сброшен) — дедупликация работает, в `events_log` только одна запись на `external_event_id`
- [ ] Документ `docs/SendPulse_Flow_Setup.docx` создан и читается в Word
- [ ] CLAUDE.md обновлён в § 7.2

---

## Do NOT

- НЕ удалять `parse_webhook` метод. Он нужен для будущего апгрейда тарифа.
- НЕ удалять `comment_to_dm.py` scenario из `app/services/scenarios/`. На текущем тарифе он не triggered (нет comments через API), но при апгрейде / смене провайдера он снова станет нужен.
- НЕ парсить `inbox_unread` как признак нового сообщения. Юля может прочитать сообщение в SendPulse UI, и `inbox_unread` обнулится до того, как мы его обработаем. Используем только `created_at > cursor`.
- НЕ делать дополнительный запрос `/contacts/get` для каждого нового контакта. Все данные уже в `/chats` response (`channel_data`).
- НЕ ставить cursor по default = `NOW() - 5min` как было в v1. На live-аккаунте Юли это даст немедленный bulk processing 95 сообщений. Только `NOW()` без backfill, с возможностью ручной правки через redis-cli.
- НЕ обрабатывать `direction: 2` (outgoing). Это наши же сообщения. Если попадут в pipeline — зациклится.
- НЕ забыть фильтр `is_echo: true`. Meta присылает копии наших исходящих в чат. Это другая форма самозацикливания.
- НЕ ставить `MAX_PAGES_PER_TICK > 5` без обсуждения. SendPulse rate limits не публикуют точные значения; больше 5 страниц подряд может вызвать 429.
- НЕ удалять backward-compat код webhook'а. Когда апгрейд тарифа произойдёт — он включится одной env-переменной.

---

## Зависимости задачи

- Task 05 v1 применён ранее (мы переписываем поверх него)
- Все основные Tasks применены (01, 03, 04, 06, 07, 08, 09, 11, 13, 14, 15, 16, 18)
- В `.env` валидные `SENDPULSE_CLIENT_ID`, `SENDPULSE_CLIENT_SECRET`, `SENDPULSE_BOT_ID`
- Подтверждено через `curl` напрямую к `https://api.sendpulse.com/instagram/chats` что endpoint работает на Free tier (проверено 2026-05-26)

---

## Что после этой задачи

После применения у нас **реально работающая полная воронка** на Free-тарифе SendPulse:

```
✅ Polling /chats каждые 30 сек — корректные endpoints
✅ Парсинг real-shape responses (text + reply_to_story)
✅ Echo/outgoing/unsupported фильтры
✅ Пагинация через links.next
✅ Send через /contacts/send с text + URL buttons
✅ Cold-start без backfill (защита от bulk processing 95 unread)
✅ Webhook endpoint готов к paid-tier (один env flip)
✅ Manual backfill инструкция в комментариях кода
✅ Документация acquisition в SendPulse Flow Builder для Юли
```

**Live тест следующим шагом:**
1. `docker compose down && docker compose up -d --force-recreate`
2. Со второго Instagram-аккаунта написать DM Юле «привет»
3. Через 30 секунд в логах — событие обработано, welcome отправлен
4. Второй аккаунт получает welcome-сообщение от бота
5. После клика «Перейти в Telegram» — попадаем в bot_purify

Если acquisition через comment «ОЧИЩЕНИЕ» нужен — Юля настраивает Flow Builder по `docs/SendPulse_Flow_Setup.docx` (15 минут работы в SendPulse UI).

После этой задачи можно переходить к **Task 18** (smoke checks + go-live).

---

**Дата создания:** 2026-05-26
**Применять в:** `D:\Work\social_inbox` поверх Task 05 v1
**Эстимейт:** 4-5 часов на Claude Code + 1-2 часа Юли на Flow Builder + 30 минут на live-тест
