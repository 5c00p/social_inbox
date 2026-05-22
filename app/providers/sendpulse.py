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
- POST /instagram/comments/reply (paid feature in some tiers)
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
        settings = get_settings()
        self._client = SendPulseClient(
            client_id,
            client_secret,
            base_url=settings.sendpulse_api_base,
        )
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
        DB layer (events_log UNIQUE constraint), so overlap is safe.
        """
        settings = get_settings()
        bot_id = settings.sendpulse_bot_id
        if not bot_id:
            log.warning("sendpulse_polling_no_bot_id")
            return []

        since = await get_cursor(bot_id)
        since_with_overlap = since.replace(microsecond=0)
        log.info("sendpulse_polling_start", since=since_with_overlap.isoformat())

        events: list[IncomingEvent] = []
        latest_ts = since_with_overlap

        try:
            msg_events, msg_latest = await self._poll_messages(bot_id, since_with_overlap)
            events.extend(msg_events)
            if msg_latest > latest_ts:
                latest_ts = msg_latest
        except SendPulseAPIError as exc:
            log.warning(
                "sendpulse_messages_poll_failed",
                status=exc.status,
                body=exc.body[:200],
            )

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
        self,
        bot_id: str,
        since: datetime,
    ) -> tuple[list[IncomingEvent], datetime]:
        """Fetch messages updated since cursor."""
        params: dict[str, Any] = {
            "bot_id": bot_id,
            "from": since.isoformat(),
            "limit": 100,
        }
        response = await self._client.request("GET", "/instagram/messages", params=params)
        items_raw = response.get("data", [])
        items: list[dict[str, Any]] = items_raw if isinstance(items_raw, list) else []

        events: list[IncomingEvent] = []
        latest_ts = since
        for item in items:
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
        self,
        bot_id: str,
        since: datetime,
    ) -> tuple[list[IncomingEvent], datetime]:
        """Fetch comments updated since cursor."""
        params: dict[str, Any] = {
            "bot_id": bot_id,
            "from": since.isoformat(),
            "limit": 100,
        }
        response = await self._client.request("GET", "/instagram/comments", params=params)
        items_raw = response.get("data", [])
        items: list[dict[str, Any]] = items_raw if isinstance(items_raw, list) else []

        events: list[IncomingEvent] = []
        latest_ts = since
        for item in items:
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
            "POST",
            "/instagram/contacts/send",
            json=payload,
        )
        return self._extract_message_id(response, context="dm")

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
                "POST",
                "/instagram/comments/reply",
                json=payload,
            )
        except SendPulseAPIError as exc:
            if exc.status == 403:
                log.info(
                    "sendpulse_comment_reply_fallback_dm",
                    comment_id=msg.reply_to_comment_id,
                )
                return await self._send_dm(msg)
            raise

        return self._extract_message_id(response, context="comment_reply")

    def _extract_message_id(
        self,
        response: dict[str, Any],
        *,
        context: str,
    ) -> str | None:
        """Pull the SendPulse message id out of a send response.

        Response shape varies between endpoints; typical:
            {"success": true, "data": [{"id": "..."}]}
        but `data` may also be a single dict. Returns None for unexpected shapes.
        """
        data = response.get("data")
        if isinstance(data, list) and data:
            first = data[0]
            if isinstance(first, dict):
                value = first.get("id") or first.get("message_id")
                if value:
                    return str(value)
        if isinstance(data, dict):
            value = data.get("id") or data.get("message_id")
            if value:
                return str(value)
        log.warning(
            "sendpulse_send_unexpected_response",
            context=context,
            response=str(response)[:200],
        )
        return None

    def _build_message_block(self, msg: OutgoingMessage) -> dict[str, Any]:
        """Build SendPulse message payload block from OutgoingMessage."""
        block: dict[str, Any] = {"type": "text", "message": {"text": msg.text or ""}}

        if msg.quick_replies:
            buttons = []
            for qr in msg.quick_replies:
                if qr.payload.startswith(("http://", "https://")):
                    buttons.append(
                        {
                            "type": "url",
                            "title": qr.title,
                            "url": qr.payload,
                        }
                    )
                else:
                    buttons.append(
                        {
                            "type": "reply",
                            "title": qr.title,
                            "payload": qr.payload,
                        }
                    )
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
                "GET",
                f"/instagram/contacts/{external_user_id}",
            )
        except SendPulseAPIError as exc:
            log.warning(
                "sendpulse_profile_failed",
                user=external_user_id,
                status=exc.status,
            )
            return {}

        data_raw: Any = response.get("data", response)
        if not isinstance(data_raw, dict):
            return {}
        return {
            "username": data_raw.get("username") or data_raw.get("user_name"),
            "full_name": data_raw.get("name") or data_raw.get("full_name"),
            "profile_pic_url": data_raw.get("photo") or data_raw.get("profile_pic_url"),
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
                raw.get("id")
                or raw.get("message_id")
                or f"msg:{external_user_id}:{raw.get('created_at')}"
            )

            text = raw.get("text")
            message_field = raw.get("message")
            if not text and isinstance(message_field, dict):
                text = message_field.get("text")

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
        except Exception as exc:
            log.exception(
                "sendpulse_message_parse_failed",
                error=str(exc),
                raw=str(raw)[:200],
            )
            return None

    def _parse_comment_item(self, raw: dict[str, Any]) -> IncomingEvent | None:
        """Convert SendPulse comment JSON to IncomingEvent."""
        try:
            contact = raw.get("from") or raw.get("user") or {}
            external_user_id = contact.get("id") or raw.get("user_id") or raw.get("author_id")
            if not external_user_id:
                log.warning("sendpulse_comment_missing_user_id", raw=str(raw)[:200])
                return None

            external_event_id = (
                raw.get("id")
                or raw.get("comment_id")
                or f"comment:{external_user_id}:{raw.get('created_at')}"
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
        except Exception as exc:
            log.exception(
                "sendpulse_comment_parse_failed",
                error=str(exc),
                raw=str(raw)[:200],
            )
            return None


def _parse_ts(value: Any) -> datetime:
    """Parse SendPulse timestamp (ISO string or unix epoch) → aware datetime."""
    if value is None:
        return datetime.now(UTC)
    if isinstance(value, int | float):
        return datetime.fromtimestamp(value, tz=UTC)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            pass
    return datetime.now(UTC)
