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
# 'reply_to_story' is treated as a regular text message (data.text contains
# the reaction text/emoji).
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
        settings = get_settings()
        self._client = SendPulseClient(
            client_id,
            client_secret,
            base_url=settings.sendpulse_api_base,
        )
        self._webhook_secret = webhook_secret

    # ──────────── Webhook (paid-tier ready, no-op on Free) ────────────

    async def parse_webhook(
        self,
        raw_body: bytes,
        headers: dict[str, str],
    ) -> list[IncomingEvent]:
        """Parse webhook payload from SendPulse.

        Active only on paid plan with webhook configured in SendPulse UI.
        On Free plan + polling, this endpoint is never hit by SendPulse —
        returns empty list defensively.

        Signature: HMAC-SHA256 of raw_body using webhook_secret, in X-Signature
        header. Webhook payload mirrors /chats item shape per OpenAPI spec.
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
            parsed = self._parse_chat_item(raw)
            if parsed:
                events.append(parsed)
        return events

    # ────────────────────────── Polling ──────────────────────────────

    async def poll_new_events(self) -> list[IncomingEvent]:
        """Fetch new chat messages since the last polling cursor.

        Strategy:
        - Walk /chats pages until either:
          (a) we've collected all chats updated since cursor, OR
          (b) we hit MAX_PAGES_PER_TICK, OR
          (c) we encounter a chat whose last_message.created_at is <= cursor
              (chats are sorted by last_activity DESC, so once we see an old
              one all subsequent ones are older too — early exit optimization)
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

                chats_raw = response.get("data") or []
                chats: list[dict[str, Any]] = chats_raw if isinstance(chats_raw, list) else []
                if not chats:
                    break

                page_events, page_latest, hit_old = self._process_chats_page(
                    chats,
                    cursor=since,
                )
                events.extend(page_events)
                if page_latest > latest_ts:
                    latest_ts = page_latest

                if hit_old:
                    log.debug("sendpulse_polling_early_exit", page=page)
                    break

                links_raw = response.get("links") or {}
                links: dict[str, Any] = links_raw if isinstance(links_raw, dict) else {}
                next_value = links.get("next")
                next_url = str(next_value) if next_value else None
                if not next_url:
                    break

        except SendPulseAuthError as exc:
            log.error("sendpulse_polling_auth_failed", error=str(exc))
            return []
        except SendPulseAPIError as exc:
            log.warning(
                "sendpulse_polling_api_error",
                status=exc.status,
                path=exc.path,
                body=exc.body[:200],
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

            if msg_ts <= cursor:
                hit_old = True
                continue

            if msg_ts > latest_ts:
                latest_ts = msg_ts

            event = self._parse_chat_item(chat)
            if event:
                events.append(event)

        return events, latest_ts, hit_old

    # ──────────────────────────── Send ───────────────────────────────

    async def send(self, msg: OutgoingMessage) -> str | None:
        """Send a DM via SendPulse.

        SendPulse REST has only one outbound endpoint — /contacts/send. There's
        no separate 'private reply to comment' on Free tier (and on paid it
        goes through Flow Builder, not REST). reply_to_comment_id is ignored;
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
                status=exc.status,
                body=exc.body[:200],
                contact_id=msg.external_user_id,
            )
            return None

        data = response.get("data")
        message_id: str | None = None
        if isinstance(data, list) and data:
            first = data[0]
            if isinstance(first, dict):
                value = first.get("id") or first.get("message_id")
                message_id = str(value) if value else None
        elif isinstance(data, dict):
            value = data.get("id") or data.get("message_id")
            message_id = str(value) if value else None

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
            buttons: list[dict[str, Any]] = []
            for qr in msg.quick_replies:
                if qr.payload.startswith(("http://", "https://")):
                    buttons.append(
                        {
                            "type": "web_url",
                            "title": qr.title,
                            "url": qr.payload,
                        }
                    )
                else:
                    buttons.append(
                        {
                            "type": "postback",
                            "title": qr.title,
                            "data": {"text": qr.payload},
                        }
                    )
            return {
                "type": "generic_template",
                "message": {
                    "attachment": {
                        "payload": {
                            "elements": [
                                {
                                    "title": msg.text or " ",
                                    "buttons": buttons,
                                }
                            ]
                        }
                    }
                },
            }

        return {"type": "text", "message": {"text": msg.text or ""}}

    # ────────────────────────── Profile ──────────────────────────────

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
                contact_id=external_user_id,
                status=exc.status,
            )
            return {}

        data_raw = response.get("data") or {}
        if not isinstance(data_raw, dict):
            return {}
        channel_raw = data_raw.get("channel_data") or {}
        channel: dict[str, Any] = channel_raw if isinstance(channel_raw, dict) else {}
        return {
            "username": channel.get("user_name"),
            "full_name": channel.get("name"),
            "profile_pic_url": channel.get("profile_pic"),
        }

    # ───────────────────────── Parsing ───────────────────────────────

    def _parse_chat_item(self, chat: dict[str, Any]) -> IncomingEvent | None:
        """Convert one /chats response item into an IncomingEvent.

        Skip rules (return None):
        - missing inbox_last_message
        - direction != 1 (not incoming)
        - is_echo == True (our own outgoing returned by Meta echo)
        - type not in SUPPORTED_TYPES
        - missing required fields (contact_id, message id, text)
        """
        last_msg = chat.get("inbox_last_message")
        if not isinstance(last_msg, dict):
            return None

        if last_msg.get("direction") != 1:
            return None

        data_raw = last_msg.get("data") or {}
        data: dict[str, Any] = data_raw if isinstance(data_raw, dict) else {}
        if data.get("is_echo") is True:
            return None

        msg_type = last_msg.get("type")
        if msg_type not in SUPPORTED_TYPES:
            log.debug(
                "sendpulse_skipping_unsupported_type",
                msg_type=msg_type,
                message_id=last_msg.get("id"),
            )
            return None

        text = data.get("text")
        if not text:
            log.debug("sendpulse_skipping_no_text", message_id=last_msg.get("id"))
            return None

        contact_raw = chat.get("contact") or {}
        contact: dict[str, Any] = contact_raw if isinstance(contact_raw, dict) else {}
        contact_id = last_msg.get("contact_id") or contact.get("id")
        if not contact_id:
            log.warning("sendpulse_missing_contact_id", message_id=last_msg.get("id"))
            return None

        message_id = last_msg.get("id")
        if not message_id:
            log.warning("sendpulse_missing_message_id", contact_id=contact_id)
            return None

        channel_raw = contact.get("channel_data") or {}
        channel: dict[str, Any] = channel_raw if isinstance(channel_raw, dict) else {}
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
            text=str(text),
            occurred_at=_parse_ts(last_msg.get("created_at")),
            raw_payload=chat,
        )


def _parse_ts(value: Any) -> datetime:
    """Parse SendPulse timestamp (ISO string or unix epoch) → aware UTC datetime."""
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
