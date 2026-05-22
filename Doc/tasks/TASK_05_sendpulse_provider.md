# Task 05: SendPulseProvider implementation (polling-based)

> Применить в `D:\Work\social_inbox` после Tasks 01, 03, 04, 06, 07. Положить файл в корень проекта, открыть в VS Code, попросить Claude Code: «Прочитай TASK_05_sendpulse_provider.md и выполни все подзадачи последовательно. Перед каждой подзадачей кратко опиши план, после — что фактически сделано».

---

## Контекст

В Task 04 мы заложили `MessagingProvider` ABC и скелет `SendPulseProvider` со всеми методами, кидающими `NotImplementedError`. Юля закончила настройку SendPulse и передала credentials.

**Важная корректировка плана:** webhook'и SendPulse доступны только в платных тарифах. В Free тарифе остаётся только REST API. Это меняет архитектуру **только** для SendPulse — `webhook endpoint` из Task 06 остаётся в коде (для будущего апгрейда тарифа), но реальная работа идёт через **polling**: воркер раз в N секунд опрашивает SendPulse API и формирует те же `IncomingEvent`, которые в идеальном мире пришли бы через webhook.

С точки зрения остальной архитектуры **ничего не меняется**:
- Engine, scenarios, safety, Claude, admin — работают как раньше
- `IncomingEvent` / `OutgoingMessage` — те же
- Worker обрабатывает события из той же arq queue
- bot_purify-интеграция, /api/lead/... — без изменений

Меняется только источник событий: вместо `POST /webhooks/sendpulse` → `polling loop in worker`.

После применения Task 05:
- SendPulseProvider умеет реально слать DM и читать события
- Polling работает раз в 30 секунд (конфигурируется)
- При апгрейде тарифа SendPulse — переключение на webhook'и через одну переменную окружения
- Тесты с замоканным httpx покрывают oauth, polling, send

---

## Цель

После выполнения этой задачи:

- `app/providers/sendpulse.py` — полная реализация трёх методов ABC + polling loop
- OAuth2 token caching в Redis (TTL 50 мин)
- Polling cursor (last_polled_at) в Redis для idempotency
- Конфигурация: `SENDPULSE_POLLING_ENABLED`, `SENDPULSE_POLLING_INTERVAL_SECONDS`, `SENDPULSE_BOT_ID`
- arq cron job `sendpulse_poller` запускается каждые 30 секунд если polling включён
- Graceful degradation: если comments endpoint возвращает 403 (Free plan) — логируем и работаем только с DM
- Параметр `SENDPULSE_WEBHOOK_SECRET` остаётся, `parse_webhook` корректно работает для платного апгрейда
- Тесты покрывают: oauth flow, polling pagination, send (text + quick_replies + reply_to_comment), retry на 5xx, обработку 403/429
- E2E тест: polling забирает fake event из mocked SendPulse → попадает в очередь → worker обрабатывает → scenario выдаёт ответ → send через SendPulse

---

## Подзадачи

### 1. Зависимости и конфигурация

a) В `pyproject.toml` убедиться, что есть `httpx` (должно быть из Task 01). Добавить `pytest-httpx` в dev dependencies если ещё нет:

```toml
# в [dependency-groups] dev:
"pytest-httpx==0.34.0",
```

b) Расширить `app/config.py` — добавить SendPulse-specific поля. Найти `Settings` и добавить:

```python
# --- SendPulse polling ---
sendpulse_polling_enabled: bool = True
sendpulse_polling_interval_seconds: int = 30
sendpulse_bot_id: str = ""  # IG bot ID from SendPulse dashboard
sendpulse_api_base: str = "https://api.sendpulse.com"
```

c) В `.env.example` добавить:

```bash
# --- SendPulse polling (Free tier workaround) ---
# Set ENABLED=false when upgrading to paid plan with webhooks
SENDPULSE_POLLING_ENABLED=true
SENDPULSE_POLLING_INTERVAL_SECONDS=30
SENDPULSE_BOT_ID=<from SendPulse → Chatbots → your Instagram bot → "Bot ID" field>
```

   Куда взять `SENDPULSE_BOT_ID`: SendPulse → Чат-боты → выбери Instagram-бот → URL содержит ID (или в настройках бота явное поле).

### 2. Refactor: вынести HTTP-клиент в отдельный модуль

a) Создать `app/providers/sendpulse_client.py` — низкоуровневая обёртка над SendPulse REST API. Отделение от провайдера даёт чистое тестирование и переиспользование.

```python
"""SendPulse REST API client.

Handles:
- OAuth2 client_credentials flow with token caching in Redis
- HTTP retries on transient errors (5xx, 429)
- Graceful 401 handling (refresh token and retry once)

Does NOT contain provider-level logic (event parsing, cursor management).
That stays in app/providers/sendpulse.py.

Docs: https://sendpulse.com/integrations/api/chatbot/instagram
"""
from __future__ import annotations

import asyncio
from typing import Any

import httpx

from app.config import get_settings
from app.repos.redis_client import get_redis
from app.utils.logging import get_logger

log = get_logger(__name__)

TOKEN_REDIS_KEY = "sendpulse:access_token"
TOKEN_TTL_SECONDS = 50 * 60  # SendPulse issues 60-min tokens; refresh 10 min early


class SendPulseAuthError(Exception):
    """OAuth or token refresh failed."""


class SendPulseAPIError(Exception):
    """Non-2xx response after retries."""

    def __init__(self, status: int, body: str) -> None:
        self.status = status
        self.body = body
        super().__init__(f"SendPulse API error {status}: {body[:200]}")


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

    # ---- Auth ----

    async def _fetch_new_token(self) -> str:
        """Request a new OAuth access token from SendPulse."""
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
        """Return cached token or fetch a new one."""
        redis = await get_redis()
        if not force_refresh:
            cached = await redis.get(TOKEN_REDIS_KEY)
            if cached:
                return cached

        token = await self._fetch_new_token()
        await redis.set(TOKEN_REDIS_KEY, token, ex=TOKEN_TTL_SECONDS)
        log.info("sendpulse_token_refreshed")
        return token

    # ---- HTTP wrapper ----

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        max_retries: int = 3,
    ) -> dict[str, Any]:
        """Perform an authenticated API request with retries on transient errors.

        Returns parsed JSON body on 2xx. Raises SendPulseAPIError otherwise.
        On 401: refreshes token once and retries.
        On 5xx / 429: exponential backoff up to max_retries.
        On 403: raises immediately (likely Free tier limitation — don't retry).
        """
        url = f"{self._base_url}{path}"

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

            # 2xx — success
            if 200 <= response.status_code < 300:
                if not response.content:
                    return {}
                return response.json()

            # 401 — refresh and retry once
            if response.status_code == 401 and attempt == 0:
                log.warning("sendpulse_401_refreshing_token")
                await self._get_token(force_refresh=True)
                continue

            # 403 — likely Free tier limit; don't retry
            if response.status_code == 403:
                raise SendPulseAPIError(403, response.text)

            # 5xx / 429 — backoff and retry
            if response.status_code in (429, 500, 502, 503, 504) and attempt < max_retries:
                backoff = 2 ** attempt
                log.warning(
                    "sendpulse_transient_error_retrying",
                    status=response.status_code,
                    attempt=attempt + 1,
                    backoff=backoff,
                )
                await asyncio.sleep(backoff)
                continue

            # All other errors
            raise SendPulseAPIError(response.status_code, response.text)

        raise SendPulseAPIError(0, f"exhausted {max_retries} retries")
```

### 3. Polling cursor в Redis

a) Создать `app/providers/sendpulse_cursor.py`:

```python
"""Polling cursor for SendPulse — tracks the last-fetched timestamp per bot.

Why per-bot: in theory we could have multiple SendPulse bots later;
keying by bot_id avoids collision.

Format in Redis:
    sendpulse:cursor:<bot_id> → ISO datetime string

On first run (no cursor), we start from NOW() − 5 minutes to avoid
historical backfill on deploy.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.repos.redis_client import get_redis


def _key(bot_id: str) -> str:
    return f"sendpulse:cursor:{bot_id}"


async def get_cursor(bot_id: str) -> datetime:
    """Return last polled timestamp, or NOW()-5min if absent (no backfill)."""
    redis = await get_redis()
    raw = await redis.get(_key(bot_id))
    if raw:
        try:
            return datetime.fromisoformat(raw)
        except ValueError:
            pass
    return datetime.now(UTC) - timedelta(minutes=5)


async def set_cursor(bot_id: str, ts: datetime) -> None:
    """Persist the latest polled timestamp."""
    redis = await get_redis()
    await redis.set(_key(bot_id), ts.isoformat())
```

### 4. SendPulseProvider — полная реализация

a) Заменить содержимое `app/providers/sendpulse.py`:

```python
"""SendPulse messaging provider.

This implementation uses **polling** instead of webhooks because SendPulse
restricts webhooks to paid plans. The webhook endpoint (parse_webhook) is kept
intact so that upgrading to a paid plan = just flip SENDPULSE_POLLING_ENABLED=false.

Polling flow:
1. arq cron job runs `sendpulse_poll_tick` every N seconds
2. provider.poll_new_events() fetches contacts/messages/comments updated since cursor
3. Each item is converted to IncomingEvent and enqueued via the same path as webhook
4. cursor advanced to latest seen timestamp

Send flow (text-only DM):
1. provider.send(msg) is called by worker after scenario engine produces a reply
2. POST /instagram/contacts/send with quick_replies if present
3. Returns SendPulse message_id on success

Comment private-reply:
- POST /instagram/comments/{comment_id}/reply (premium feature in some tiers)
- On 403: fallback to sending regular DM and logging the limitation
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

    # ---- Webhook (kept for paid-tier future) ----

    async def parse_webhook(
        self,
        raw_body: bytes,
        headers: dict[str, str],
    ) -> list[IncomingEvent]:
        """Parse webhook payload from SendPulse.

        Active only when the account is on a paid plan and webhook is configured
        in SendPulse dashboard. With free plan + polling, this endpoint exists
        but is never hit by SendPulse — returns empty list defensively.

        Signature check: HMAC-SHA256 of raw_body using webhook_secret,
        compared against the X-Signature header in constant-time.
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
            raw_body,
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(expected, signature):
            log.warning("sendpulse_webhook_invalid_signature")
            return []

        try:
            payload = json.loads(raw_body)
        except json.JSONDecodeError:
            log.warning("sendpulse_webhook_invalid_json")
            return []

        # SendPulse sends arrays of events or a single event wrapped
        events_raw = payload if isinstance(payload, list) else payload.get("data", [])
        events: list[IncomingEvent] = []
        for raw in events_raw:
            parsed = self._parse_event_item(raw)
            if parsed:
                events.append(parsed)
        return events

    # ---- Polling ----

    async def poll_new_events(self) -> list[IncomingEvent]:
        """Fetch new contacts/messages/comments since last cursor.

        Returns list of IncomingEvent ready to enqueue.
        Idempotency: events with external_event_id seen before are deduped at
        worker layer (events_log UNIQUE constraint), so overlap is safe.
        """
        settings = get_settings()
        bot_id = settings.sendpulse_bot_id
        if not bot_id:
            log.warning("sendpulse_polling_no_bot_id")
            return []

        since = await get_cursor(bot_id)
        # Overlap window: re-fetch last 60s to catch race conditions
        since_with_overlap = since.replace(microsecond=0)
        log.info("sendpulse_polling_start", since=since_with_overlap.isoformat())

        events: list[IncomingEvent] = []
        latest_ts = since_with_overlap

        # 1. Poll messages (DMs)
        try:
            msg_events, msg_latest = await self._poll_messages(bot_id, since_with_overlap)
            events.extend(msg_events)
            if msg_latest > latest_ts:
                latest_ts = msg_latest
        except SendPulseAPIError as exc:
            log.warning("sendpulse_messages_poll_failed", status=exc.status, body=exc.body[:200])

        # 2. Poll comments (may fail with 403 on Free tier — degrade gracefully)
        try:
            cmt_events, cmt_latest = await self._poll_comments(bot_id, since_with_overlap)
            events.extend(cmt_events)
            if cmt_latest > latest_ts:
                latest_ts = cmt_latest
        except SendPulseAPIError as exc:
            if exc.status == 403:
                log.info(
                    "sendpulse_comments_unavailable_free_tier",
                    detail="Comments endpoint requires paid plan; skipping",
                )
            else:
                log.warning("sendpulse_comments_poll_failed", status=exc.status)

        await set_cursor(bot_id, latest_ts)
        log.info(
            "sendpulse_polling_done",
            events_count=len(events),
            new_cursor=latest_ts.isoformat(),
        )
        return events

    async def _poll_messages(
        self, bot_id: str, since: datetime,
    ) -> tuple[list[IncomingEvent], datetime]:
        """Fetch messages updated since cursor."""
        params = {
            "bot_id": bot_id,
            "from": since.isoformat(),
            "limit": 100,
        }
        response = await self._client.request("GET", "/instagram/messages", params=params)
        items = response.get("data", []) if isinstance(response, dict) else response

        events: list[IncomingEvent] = []
        latest_ts = since
        for item in items or []:
            # Only inbound messages (filter out our own outgoing if API returns mixed)
            direction = item.get("direction", "in")
            if direction != "in":
                continue
            event = self._parse_message_item(item)
            if event:
                events.append(event)
                if event.occurred_at > latest_ts:
                    latest_ts = event.occurred_at
        return events, latest_ts

    async def _poll_comments(
        self, bot_id: str, since: datetime,
    ) -> tuple[list[IncomingEvent], datetime]:
        """Fetch comments updated since cursor."""
        params = {
            "bot_id": bot_id,
            "from": since.isoformat(),
            "limit": 100,
        }
        response = await self._client.request("GET", "/instagram/comments", params=params)
        items = response.get("data", []) if isinstance(response, dict) else response

        events: list[IncomingEvent] = []
        latest_ts = since
        for item in items or []:
            event = self._parse_comment_item(item)
            if event:
                events.append(event)
                if event.occurred_at > latest_ts:
                    latest_ts = event.occurred_at
        return events, latest_ts

    # ---- Send ----

    async def send(self, msg: OutgoingMessage) -> str | None:
        """Send DM or comment private-reply via SendPulse.

        Returns SendPulse message_id on success, None on failure.
        """
        try:
            if msg.reply_to_comment_id:
                return await self._send_comment_reply(msg)
            else:
                return await self._send_dm(msg)
        except SendPulseAuthError as exc:
            log.error("sendpulse_send_auth_failed", error=str(exc))
            return None
        except SendPulseAPIError as exc:
            log.warning(
                "sendpulse_send_api_error",
                status=exc.status,
                body=exc.body[:200],
                user=msg.external_user_id,
            )
            return None

    async def _send_dm(self, msg: OutgoingMessage) -> str | None:
        """POST /instagram/contacts/send for direct DM."""
        payload: dict[str, Any] = {
            "contact_id": msg.external_user_id,
            "messages": [self._build_message_block(msg)],
        }
        response = await self._client.request(
            "POST", "/instagram/contacts/send", json=payload,
        )
        # Response shape varies; typical: {"success": true, "data": [{"id": "..."}]}
        data = response.get("data")
        if isinstance(data, list) and data:
            return str(data[0].get("id") or data[0].get("message_id") or "")
        if isinstance(data, dict):
            return str(data.get("id") or data.get("message_id") or "")
        log.warning("sendpulse_send_dm_unexpected_response", response=str(response)[:200])
        return None

    async def _send_comment_reply(self, msg: OutgoingMessage) -> str | None:
        """Send a private reply tied to a comment.

        Falls back to a regular DM if SendPulse rejects (403 — Free tier).
        """
        payload: dict[str, Any] = {
            "comment_id": msg.reply_to_comment_id,
            "messages": [self._build_message_block(msg)],
        }
        try:
            response = await self._client.request(
                "POST", "/instagram/comments/reply", json=payload,
            )
        except SendPulseAPIError as exc:
            if exc.status == 403:
                log.info(
                    "sendpulse_comment_reply_fallback_dm",
                    comment_id=msg.reply_to_comment_id,
                )
                return await self._send_dm(msg)
            raise

        data = response.get("data")
        if isinstance(data, list) and data:
            return str(data[0].get("id") or "")
        if isinstance(data, dict):
            return str(data.get("id") or "")
        return None

    def _build_message_block(self, msg: OutgoingMessage) -> dict[str, Any]:
        """Build SendPulse message payload block from OutgoingMessage."""
        block: dict[str, Any] = {"type": "text", "message": {"text": msg.text or ""}}

        if msg.quick_replies:
            buttons = []
            for qr in msg.quick_replies:
                # SendPulse supports URL buttons and quick-reply buttons differently;
                # if payload looks like a URL — render as URL button
                if qr.payload.startswith(("http://", "https://")):
                    buttons.append({
                        "type": "url",
                        "title": qr.title,
                        "url": qr.payload,
                    })
                else:
                    buttons.append({
                        "type": "reply",
                        "title": qr.title,
                        "payload": qr.payload,
                    })
            block["message"]["buttons"] = buttons

        return block

    # ---- Profile ----

    async def fetch_user_profile(
        self,
        platform: Platform,
        external_user_id: str,
    ) -> dict[str, Any]:
        """Return contact info from SendPulse.

        Returns empty dict if API call fails or contact not found.
        Polling normally pre-populates this data into IncomingEvent already.
        """
        try:
            response = await self._client.request(
                "GET", f"/instagram/contacts/{external_user_id}",
            )
        except SendPulseAPIError as exc:
            log.warning(
                "sendpulse_profile_failed",
                user=external_user_id,
                status=exc.status,
            )
            return {}

        data = response.get("data", response) if isinstance(response, dict) else {}
        if not isinstance(data, dict):
            return {}
        return {
            "username": data.get("username") or data.get("user_name"),
            "full_name": data.get("name") or data.get("full_name"),
            "profile_pic_url": data.get("photo") or data.get("profile_pic_url"),
        }

    # ---- Parsing helpers ----

    def _parse_event_item(self, raw: dict[str, Any]) -> IncomingEvent | None:
        """Dispatch parsing based on event type field (webhook payload format)."""
        event_type = raw.get("type") or raw.get("event_type")
        if event_type in ("message", "incoming_message"):
            return self._parse_message_item(raw)
        if event_type in ("comment", "incoming_comment"):
            return self._parse_comment_item(raw)
        log.warning("sendpulse_unknown_event_type", event_type=event_type)
        return None

    def _parse_message_item(self, raw: dict[str, Any]) -> IncomingEvent | None:
        """Convert SendPulse message JSON to IncomingEvent."""
        try:
            contact = raw.get("contact") or raw.get("subscriber") or {}
            external_user_id = (
                contact.get("id")
                or contact.get("external_id")
                or raw.get("contact_id")
                or raw.get("user_id")
            )
            if not external_user_id:
                log.warning("sendpulse_message_missing_user_id", raw=str(raw)[:200])
                return None

            external_event_id = (
                raw.get("id") or raw.get("message_id") or f"msg:{external_user_id}:{raw.get('created_at')}"
            )
            text = raw.get("text") or raw.get("message", {}).get("text") if isinstance(raw.get("message"), dict) else raw.get("text")
            occurred_at = _parse_ts(raw.get("created_at") or raw.get("date"))

            return IncomingEvent(
                provider="sendpulse",
                platform="instagram",
                event_type="message",
                external_user_id=str(external_user_id),
                external_event_id=str(external_event_id),
                username=contact.get("username") or contact.get("user_name"),
                full_name=contact.get("name") or contact.get("full_name"),
                text=text,
                occurred_at=occurred_at,
                raw_payload=raw,
            )
        except Exception as exc:  # noqa: BLE001
            log.exception("sendpulse_message_parse_failed", error=str(exc), raw=str(raw)[:200])
            return None

    def _parse_comment_item(self, raw: dict[str, Any]) -> IncomingEvent | None:
        """Convert SendPulse comment JSON to IncomingEvent."""
        try:
            contact = raw.get("from") or raw.get("user") or {}
            external_user_id = (
                contact.get("id")
                or raw.get("user_id")
                or raw.get("author_id")
            )
            if not external_user_id:
                log.warning("sendpulse_comment_missing_user_id", raw=str(raw)[:200])
                return None

            external_event_id = (
                raw.get("id") or raw.get("comment_id") or f"comment:{external_user_id}:{raw.get('created_at')}"
            )
            occurred_at = _parse_ts(raw.get("created_at") or raw.get("date"))

            return IncomingEvent(
                provider="sendpulse",
                platform="instagram",
                event_type="comment",
                external_user_id=str(external_user_id),
                external_event_id=str(external_event_id),
                username=contact.get("username"),
                full_name=contact.get("name") or contact.get("full_name"),
                text=raw.get("text") or raw.get("message"),
                post_id=str(raw.get("post_id") or raw.get("media_id") or ""),
                comment_id=str(raw.get("id") or raw.get("comment_id") or ""),
                occurred_at=occurred_at,
                raw_payload=raw,
            )
        except Exception as exc:  # noqa: BLE001
            log.exception("sendpulse_comment_parse_failed", error=str(exc), raw=str(raw)[:200])
            return None


def _parse_ts(value: Any) -> datetime:
    """Parse SendPulse timestamp (ISO string or unix epoch) → aware datetime."""
    if value is None:
        return datetime.now(UTC)
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=UTC)
    if isinstance(value, str):
        try:
            # ISO with Z suffix
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            pass
    return datetime.now(UTC)
```

### 5. Poller task в arq

a) Создать `app/workers/tasks_sendpulse.py`:

```python
"""arq task: poll SendPulse for new events.

Runs every N seconds (configured by SENDPULSE_POLLING_INTERVAL_SECONDS).
Enqueues IncomingEvents into the same queue used by webhook handler,
so downstream worker (process_incoming_event) is provider-agnostic.

Skipped if SENDPULSE_POLLING_ENABLED=false (paid plan with webhooks).
"""
from __future__ import annotations

from typing import Any

from app.config import get_settings
from app.providers import get_provider
from app.providers.sendpulse import SendPulseProvider
from app.repos import events as events_repo
from app.utils.logging import get_logger
from app.workers.enqueue import enqueue_event

log = get_logger(__name__)


async def sendpulse_poll_tick(ctx: dict[str, Any]) -> None:
    """One polling iteration. Idempotent — safe to run more often than needed."""
    settings = get_settings()
    if not settings.sendpulse_polling_enabled:
        return
    if settings.messaging_provider != "sendpulse":
        return

    provider = get_provider()
    if not isinstance(provider, SendPulseProvider):
        log.warning("sendpulse_poller_provider_mismatch", actual=type(provider).__name__)
        return

    try:
        events = await provider.poll_new_events()
    except Exception as exc:  # noqa: BLE001
        log.exception("sendpulse_poll_failed", error=str(exc))
        return

    if not events:
        return

    log.info("sendpulse_poll_events_enqueueing", count=len(events))
    for event in events:
        try:
            row = await events_repo.insert(
                provider_name=event.provider,
                platform=event.platform,
                event_type=event.event_type,
                external_event_id=event.external_event_id,
                payload=event.raw_payload,
                signature_valid=True,  # polling = trusted source
            )
            await enqueue_event(event, row["id"])
        except Exception as exc:  # noqa: BLE001
            # Most likely UniqueViolationError on external_event_id — dedup
            log.debug(
                "sendpulse_poll_event_dedup_or_error",
                external_event_id=event.external_event_id,
                error=str(exc)[:100],
            )
```

### 6. Регистрация polling cron в arq

a) Обновить `app/workers/arq_settings.py` — добавить cron-job для poller'а. Найти существующий `cron_jobs` блок и расширить:

```python
from app.workers.tasks_sendpulse import sendpulse_poll_tick

# В WorkerSettings.functions добавить:
functions: list[Any] = [
    process_incoming_event,
    watchdog_check,
    daily_digest,
    sendpulse_poll_tick,
]

# В cron_jobs добавить (interval из settings):
cron_jobs = [
    # ... existing cron jobs ...
    # SendPulse poller: every N seconds (default 30s)
    # Note: arq cron has minute-resolution; for sub-minute polling we use
    # multiple cron entries spread across the minute.
    cron(sendpulse_poll_tick, second={0, 30}, run_at_startup=True),
]
```

   **Замечание про интервалы:** arq cron поддерживает поле `second` для sub-minute расписания. По умолчанию `{0, 30}` = каждые 30 секунд. Если `SENDPULSE_POLLING_INTERVAL_SECONDS=15` — нужно `{0, 15, 30, 45}`. Это статический список — менять через изменение кода. Для проекта Юли 30 секунд — разумный default, и менять часто не понадобится.

### 7. Webhook endpoint остаётся работоспособным

a) Никаких изменений в `app/api/webhooks.py`. Endpoint работает: при платном тарифе SendPulse начнёт слать POST'ы, мы их парсим (через `SendPulseProvider.parse_webhook`), кладём в очередь. Параллельно polling может остаться включённым (idempotency защитит от дублей через `events_log` UNIQUE INDEX).

   **На production:** когда апгрейдишь SendPulse до платного тарифа — установи `SENDPULSE_POLLING_ENABLED=false` в `.env` и `restart worker`. Webhook сам начнёт работать.

### 8. Документация в CLAUDE.md

a) В CLAUDE.md в § 7 (или где описан SendPulse) добавить:

```markdown
### 7.2. SendPulse — polling vs webhook

SendPulse webhooks доступны только в платных тарифах. До апгрейда работаем
на polling-режиме:

- Worker раз в 30 сек дёргает `GET /instagram/messages` и `GET /instagram/comments`
- Новые события приходят с задержкой до 30 секунд (приемлемо для воронки)
- Cursor хранится в Redis (`sendpulse:cursor:<bot_id>`)
- Дедупликация через `events_log.external_event_id` UNIQUE
- При апгрейде тарифа: `SENDPULSE_POLLING_ENABLED=false` + restart worker

См. `app/providers/sendpulse.py:poll_new_events` и `app/workers/tasks_sendpulse.py`.

При смене провайдера (Manychat / Meta direct) → меняется один файл провайдера,
polling-логика SendPulse-specific и в новом провайдере не нужна.
```

### 9. Тесты

a) Создать `tests/test_sendpulse_client.py`:

```python
"""Tests for low-level SendPulse HTTP client."""
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
        json={"access_token": "test_token_abc", "expires_in": 3600},
    )
    client = SendPulseClient("cid", "csecret")
    token = await client._get_token()
    assert token == "test_token_abc"


@pytest.mark.asyncio
async def test_oauth_uses_cache(httpx_mock: HTTPXMock) -> None:
    # First call: fetch
    httpx_mock.add_response(
        method="POST",
        url="https://api.sendpulse.com/oauth/access_token",
        json={"access_token": "cached_token"},
    )
    client = SendPulseClient("cid", "csecret")
    t1 = await client._get_token()
    # Second call: should hit cache (no new HTTP request added; pytest-httpx
    # would fail if an unexpected request is made)
    t2 = await client._get_token()
    assert t1 == t2 == "cached_token"


@pytest.mark.asyncio
async def test_oauth_failed_raises(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        method="POST",
        url="https://api.sendpulse.com/oauth/access_token",
        status_code=401,
        text="invalid credentials",
    )
    client = SendPulseClient("bad", "bad")
    with pytest.raises(SendPulseAuthError):
        await client._get_token()


@pytest.mark.asyncio
async def test_request_retries_on_500(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        method="POST",
        url="https://api.sendpulse.com/oauth/access_token",
        json={"access_token": "tok"},
    )
    # First call: 500 → retry
    httpx_mock.add_response(
        method="GET",
        url="https://api.sendpulse.com/instagram/messages",
        status_code=500,
        text="server error",
    )
    # Second call: success
    httpx_mock.add_response(
        method="GET",
        url="https://api.sendpulse.com/instagram/messages",
        json={"data": []},
    )
    client = SendPulseClient("cid", "csecret")
    result = await client.request("GET", "/instagram/messages")
    assert result == {"data": []}


@pytest.mark.asyncio
async def test_request_no_retry_on_403(httpx_mock: HTTPXMock) -> None:
    """Free tier limitation — don't waste retries."""
    httpx_mock.add_response(
        method="POST",
        url="https://api.sendpulse.com/oauth/access_token",
        json={"access_token": "tok"},
    )
    httpx_mock.add_response(
        method="GET",
        url="https://api.sendpulse.com/instagram/comments",
        status_code=403,
        text="forbidden — paid plan required",
    )
    client = SendPulseClient("cid", "csecret")
    with pytest.raises(SendPulseAPIError) as exc_info:
        await client.request("GET", "/instagram/comments")
    assert exc_info.value.status == 403


@pytest.mark.asyncio
async def test_request_refreshes_token_on_401(httpx_mock: HTTPXMock) -> None:
    # OAuth twice (initial + refresh after 401)
    httpx_mock.add_response(
        method="POST",
        url="https://api.sendpulse.com/oauth/access_token",
        json={"access_token": "first_token"},
    )
    # 401 on first attempt
    httpx_mock.add_response(
        method="GET",
        url="https://api.sendpulse.com/instagram/messages",
        status_code=401,
    )
    # OAuth refresh
    httpx_mock.add_response(
        method="POST",
        url="https://api.sendpulse.com/oauth/access_token",
        json={"access_token": "refreshed_token"},
    )
    # Success on retry
    httpx_mock.add_response(
        method="GET",
        url="https://api.sendpulse.com/instagram/messages",
        json={"data": [{"id": 1}]},
    )
    client = SendPulseClient("cid", "csecret")
    result = await client.request("GET", "/instagram/messages")
    assert result == {"data": [{"id": 1}]}
```

b) Создать `tests/test_sendpulse_provider.py`:

```python
"""Tests for SendPulseProvider — parsing, polling, sending."""
from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from pytest_httpx import HTTPXMock

from app.models.events import OutgoingMessage, QuickReply
from app.providers.sendpulse import SendPulseProvider
from app.providers.sendpulse_cursor import set_cursor
from app.repos.redis_client import get_redis


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
    return SendPulseProvider(
        client_id="test_cid",
        client_secret="test_csecret",
        webhook_secret="test_wsecret",
    )


# ---- parse_message_item ----

def test_parse_message_item_basic() -> None:
    provider = _make_provider()
    raw = {
        "id": "msg_123",
        "contact": {"id": "user_456", "username": "anna", "name": "Anna P"},
        "text": "Привет",
        "created_at": "2026-05-15T12:00:00Z",
    }
    event = provider._parse_message_item(raw)
    assert event is not None
    assert event.external_user_id == "user_456"
    assert event.external_event_id == "msg_123"
    assert event.username == "anna"
    assert event.full_name == "Anna P"
    assert event.text == "Привет"
    assert event.event_type == "message"
    assert event.platform == "instagram"


def test_parse_message_item_missing_user_id_returns_none() -> None:
    provider = _make_provider()
    raw = {"id": "msg_X", "text": "hi"}  # no contact
    assert provider._parse_message_item(raw) is None


def test_parse_comment_item_basic() -> None:
    provider = _make_provider()
    raw = {
        "id": "cmt_999",
        "from": {"id": "user_777", "username": "bob"},
        "text": "ОЧИЩЕНИЕ",
        "post_id": "post_42",
        "created_at": 1715774400,  # unix epoch
    }
    event = provider._parse_comment_item(raw)
    assert event is not None
    assert event.event_type == "comment"
    assert event.external_user_id == "user_777"
    assert event.post_id == "post_42"
    assert event.comment_id == "cmt_999"
    assert event.text == "ОЧИЩЕНИЕ"


# ---- send ----

@pytest.mark.asyncio
async def test_send_dm_text(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        method="POST",
        url="https://api.sendpulse.com/oauth/access_token",
        json={"access_token": "tok"},
    )
    httpx_mock.add_response(
        method="POST",
        url="https://api.sendpulse.com/instagram/contacts/send",
        json={"success": True, "data": [{"id": "sent_msg_xyz"}]},
    )

    provider = _make_provider()
    msg = OutgoingMessage(
        platform="instagram",
        external_user_id="user_1",
        text="Привет!",
    )
    result = await provider.send(msg)
    assert result == "sent_msg_xyz"

    # Verify request payload
    requests = httpx_mock.get_requests(url="https://api.sendpulse.com/instagram/contacts/send")
    assert len(requests) == 1
    body = json.loads(requests[0].content)
    assert body["contact_id"] == "user_1"
    assert body["messages"][0]["message"]["text"] == "Привет!"


@pytest.mark.asyncio
async def test_send_dm_with_quick_replies(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        method="POST",
        url="https://api.sendpulse.com/oauth/access_token",
        json={"access_token": "tok"},
    )
    httpx_mock.add_response(
        method="POST",
        url="https://api.sendpulse.com/instagram/contacts/send",
        json={"data": [{"id": "qr_msg"}]},
    )
    provider = _make_provider()
    msg = OutgoingMessage(
        platform="instagram",
        external_user_id="user_1",
        text="Выбери:",
        quick_replies=[
            QuickReply(title="Перейти в TG", payload="https://t.me/yuliya_purify_bot?start=ig_xxx_purify"),
            QuickReply(title="Узнать больше", payload="more_info"),
        ],
    )
    result = await provider.send(msg)
    assert result == "qr_msg"

    body = json.loads(httpx_mock.get_requests(url="https://api.sendpulse.com/instagram/contacts/send")[0].content)
    buttons = body["messages"][0]["message"]["buttons"]
    assert len(buttons) == 2
    assert buttons[0]["type"] == "url"  # URL detected
    assert buttons[0]["url"].startswith("https://t.me/")
    assert buttons[1]["type"] == "reply"
    assert buttons[1]["payload"] == "more_info"


@pytest.mark.asyncio
async def test_send_comment_reply_fallback_on_403(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        method="POST",
        url="https://api.sendpulse.com/oauth/access_token",
        json={"access_token": "tok"},
    )
    # First attempt: comments/reply → 403
    httpx_mock.add_response(
        method="POST",
        url="https://api.sendpulse.com/instagram/comments/reply",
        status_code=403,
        text="paid feature",
    )
    # Fallback: regular DM send
    httpx_mock.add_response(
        method="POST",
        url="https://api.sendpulse.com/instagram/contacts/send",
        json={"data": [{"id": "fallback_msg"}]},
    )

    provider = _make_provider()
    msg = OutgoingMessage(
        platform="instagram",
        external_user_id="user_1",
        text="Спасибо за комментарий!",
        reply_to_comment_id="comment_999",
    )
    result = await provider.send(msg)
    assert result == "fallback_msg"


@pytest.mark.asyncio
async def test_send_returns_none_on_persistent_5xx(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        method="POST",
        url="https://api.sendpulse.com/oauth/access_token",
        json={"access_token": "tok"},
    )
    # Three failures (exhausts retries)
    for _ in range(4):
        httpx_mock.add_response(
            method="POST",
            url="https://api.sendpulse.com/instagram/contacts/send",
            status_code=500,
        )

    provider = _make_provider()
    msg = OutgoingMessage(platform="instagram", external_user_id="u", text="x")
    result = await provider.send(msg)
    assert result is None


# ---- poll_new_events ----

@pytest.mark.asyncio
async def test_poll_returns_events_and_advances_cursor(
    httpx_mock: HTTPXMock, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SENDPULSE_BOT_ID", "bot_test_1")
    from app.config import get_settings
    get_settings.cache_clear()

    httpx_mock.add_response(
        method="POST",
        url="https://api.sendpulse.com/oauth/access_token",
        json={"access_token": "tok"},
    )
    # messages endpoint
    httpx_mock.add_response(
        method="GET",
        url__startswith="https://api.sendpulse.com/instagram/messages",
        json={"data": [
            {
                "id": "msg_a",
                "contact": {"id": "u_1", "username": "anna"},
                "text": "hi",
                "created_at": "2026-05-15T14:00:00Z",
                "direction": "in",
            },
        ]},
    )
    # comments endpoint — Free tier returns 403
    httpx_mock.add_response(
        method="GET",
        url__startswith="https://api.sendpulse.com/instagram/comments",
        status_code=403,
        text="paid feature",
    )

    provider = _make_provider()
    events = await provider.poll_new_events()
    assert len(events) == 1
    assert events[0].external_user_id == "u_1"
    assert events[0].event_type == "message"

    # Cursor advanced
    from app.providers.sendpulse_cursor import get_cursor
    new_cursor = await get_cursor("bot_test_1")
    assert new_cursor.year == 2026


@pytest.mark.asyncio
async def test_poll_filters_outgoing_messages(
    httpx_mock: HTTPXMock, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Polling should NOT return our own outgoing messages as IncomingEvent."""
    monkeypatch.setenv("SENDPULSE_BOT_ID", "bot_test_2")
    from app.config import get_settings
    get_settings.cache_clear()

    httpx_mock.add_response(
        method="POST",
        url="https://api.sendpulse.com/oauth/access_token",
        json={"access_token": "tok"},
    )
    httpx_mock.add_response(
        method="GET",
        url__startswith="https://api.sendpulse.com/instagram/messages",
        json={"data": [
            {
                "id": "msg_in",
                "contact": {"id": "u_1"},
                "text": "hi",
                "created_at": "2026-05-15T14:00:00Z",
                "direction": "in",
            },
            {
                "id": "msg_out",
                "contact": {"id": "u_1"},
                "text": "bot reply",
                "created_at": "2026-05-15T14:00:30Z",
                "direction": "out",
            },
        ]},
    )
    httpx_mock.add_response(
        method="GET",
        url__startswith="https://api.sendpulse.com/instagram/comments",
        status_code=403,
    )

    provider = _make_provider()
    events = await provider.poll_new_events()
    assert len(events) == 1
    assert events[0].external_event_id == "msg_in"


# ---- parse_webhook ----

def test_parse_webhook_no_secret_returns_empty() -> None:
    provider = SendPulseProvider("cid", "csecret", webhook_secret="")
    # Even with valid-looking body — no secret means polling mode
    import asyncio
    result = asyncio.run(provider.parse_webhook(b'[]', {"x-signature": "anything"}))
    assert result == []


@pytest.mark.asyncio
async def test_parse_webhook_invalid_signature() -> None:
    provider = _make_provider()
    body = b'[{"type": "message", "id": "x"}]'
    result = await provider.parse_webhook(body, {"x-signature": "totally_wrong"})
    assert result == []


@pytest.mark.asyncio
async def test_parse_webhook_valid_signature() -> None:
    import hashlib
    import hmac
    provider = _make_provider()
    body = json.dumps([{
        "type": "message",
        "id": "msg_w_1",
        "contact": {"id": "u_w", "username": "x"},
        "text": "from webhook",
        "created_at": "2026-05-15T14:00:00Z",
    }]).encode()
    signature = hmac.new(b"test_wsecret", body, hashlib.sha256).hexdigest()

    events = await provider.parse_webhook(body, {"x-signature": signature})
    assert len(events) == 1
    assert events[0].external_user_id == "u_w"
```

c) Создать `tests/test_e2e_polling.py`:

```python
"""E2E: polling → events_log → worker → scenario → outgoing send."""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from pytest_httpx import HTTPXMock

from app.providers import get_provider, reset_provider
from app.providers.sendpulse import SendPulseProvider
from app.repos import users
from app.repos.redis_client import get_redis
from app.workers.tasks_sendpulse import sendpulse_poll_tick
from app.workers.tasks_messages import process_incoming_event


@pytest.fixture(autouse=True)
async def _setup(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MESSAGING_PROVIDER", "sendpulse")
    monkeypatch.setenv("SENDPULSE_POLLING_ENABLED", "true")
    monkeypatch.setenv("SENDPULSE_BOT_ID", "bot_e2e")
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
async def test_polling_creates_user_via_pipeline(
    httpx_mock: HTTPXMock, db,
) -> None:
    httpx_mock.add_response(
        method="POST",
        url="https://api.sendpulse.com/oauth/access_token",
        json={"access_token": "tok"},
    )
    httpx_mock.add_response(
        method="GET",
        url__startswith="https://api.sendpulse.com/instagram/messages",
        json={"data": [{
            "id": "e2e_msg_1",
            "contact": {"id": "e2e_user_1", "username": "anna", "name": "Anna"},
            "text": "Привет",
            "created_at": "2026-05-15T14:00:00Z",
            "direction": "in",
        }]},
    )
    httpx_mock.add_response(
        method="GET",
        url__startswith="https://api.sendpulse.com/instagram/comments",
        status_code=403,
    )
    # Outgoing send (welcome reply)
    httpx_mock.add_response(
        method="POST",
        url="https://api.sendpulse.com/instagram/contacts/send",
        json={"data": [{"id": "outgoing_sent_1"}]},
    )

    # 1. Poll
    await sendpulse_poll_tick({})

    # events_log row exists
    log_row = await db.fetchrow(
        "SELECT * FROM events_log WHERE external_event_id = 'e2e_msg_1'",
    )
    assert log_row is not None
    assert log_row["processed_at"] is None  # not yet processed

    # 2. Manually drive the worker (in real life arq dequeues)
    from app.models.events import IncomingEvent
    event = IncomingEvent.model_validate(log_row["payload"] | {
        "provider": "sendpulse",
        "platform": "instagram",
        "event_type": "message",
        "external_user_id": "e2e_user_1",
        "external_event_id": "e2e_msg_1",
        "occurred_at": "2026-05-15T14:00:00Z",
        "text": "Привет",
        "username": "anna",
        "full_name": "Anna",
        "raw_payload": log_row["payload"],
    })
    await process_incoming_event({}, event.model_dump(mode="json"), log_row["id"])

    # 3. User created
    user = await users.get_by_external("sendpulse", "instagram", "e2e_user_1")
    assert user is not None
    assert user["username"] == "anna"
```

---

## Acceptance criteria

- [ ] Файлы созданы по структуре подзадач 1–6
- [ ] `make lint` проходит
- [ ] `make test` проходит, все новые тесты зелёные:
  - `test_sendpulse_client.py` — 6 тестов
  - `test_sendpulse_provider.py` — 11 тестов
  - `test_e2e_polling.py` — 1 ключевой тест
  - Все существующие тесты Tasks 01–17 продолжают работать
- [ ] При `SENDPULSE_POLLING_ENABLED=true` и валидных credentials в `.env`:
  ```bash
  docker compose up -d
  docker compose logs worker --tail 100 | grep sendpulse_polling
  ```
  Видно строки `sendpulse_polling_start` и `sendpulse_polling_done` раз в 30 сек
- [ ] При успешном получении токена в логах `sendpulse_token_refreshed`
- [ ] При первом polling cursor советует `NOW()-5min`, повторное polling использует cursor из Redis
- [ ] При 403 на comments в логах `sendpulse_comments_unavailable_free_tier` (не warning, info-уровень)
- [ ] **Ручная end-to-end проверка** (требует валидных credentials и SendPulse Free-аккаунта):
  1. Юля отправляет DM боту со второго аккаунта
  2. В течение 30 секунд в логах `sendpulse_polling_events_enqueueing count=1`
  3. В БД появляется новый `social_users` + `messages` запись
  4. Если welcome ещё не sent — outgoing DM отправляется (видно по `outgoing_sent send_ok=true`)
  5. Второй аккаунт получает welcome-сообщение в Instagram

---

## Do NOT

- НЕ удалять webhook endpoint и `parse_webhook`. Они нужны для платного апгрейда.
- НЕ хранить SendPulse credentials в коде/тестах. Только через env, в тестах — фейковые.
- НЕ кэшировать OAuth токен в памяти провайдера. Только в Redis. Worker может переплыть инстанс — кэш в памяти потеряется.
- НЕ полить чаще раза в 15 секунд. SendPulse имеет rate limits (точные не публикуют), при чрезмерной нагрузке начнут возвращать 429.
- НЕ парсить timestamps в локальном TZ. Только UTC. `_parse_ts` уже это обеспечивает.
- НЕ ретраить на 403. Это Free tier limit — retry не поможет, только тратит API quota.
- НЕ предполагать структуру SendPulse response — она может варьироваться. Используй `.get()` с fallback'ами и логируй неожиданное.
- НЕ удалять `webhook_secret` параметр конструктора. Когда апгрейд — он понадобится. Сейчас `""` валидно.
- НЕ комбинировать polling и webhook одновременно в проде. Idempotency защищает, но это удвоенный API quota расход. На апгрейде: `SENDPULSE_POLLING_ENABLED=false` сразу.

---

## Зависимости задачи

- Tasks 01, 03, 04, 06, 07 применены (минимум для работы pipeline)
- В `.env` валидные `SENDPULSE_CLIENT_ID`, `SENDPULSE_CLIENT_SECRET`, `SENDPULSE_BOT_ID`
- Если ещё применены Tasks 08, 09, 13, 14, 15, 16 — лучше: polling сразу будет триггерить welcome/comment-to-DM/smart-сценарии в проде
- SendPulse Free аккаунт активирован, Instagram bot подключён
- (Опционально для тестов) Второй Instagram аккаунт для ручных проверок

---

## Что после этой задачи

После применения у нас работает **реальная отправка через SendPulse**:

```
✅ OAuth token caching в Redis с TTL 50 мин
✅ Polling раз в 30 сек на messages + comments
✅ Graceful degradation на 403 (comments недоступны в Free)
✅ Retry на 5xx, no-retry на 403
✅ Send DM текстом + quick replies (URL и postback типы)
✅ Comment private reply с fallback на DM при 403
✅ Webhook endpoint остаётся работоспособным для апгрейда тарифа
```

Дальше:

- **Task 17** — если ещё не сделан, развёртывание в проде
- **Task 18** — go-live checklist
- При апгрейде SendPulse тарифа: `SENDPULSE_POLLING_ENABLED=false`, перезагрузка worker, webhook автоматически активен

После Task 05 + 17 — проект **полностью функционален и развёрнут**.

---

**Дата создания:** 2026-05-15
**Применять в:** `D:\Work\social_inbox` после Tasks 01, 03, 04, 06, 07 (минимум)
**Эстимейт:** 5-7 часов на Claude Code + 1-2 часа на ручное тестирование с Юлей
