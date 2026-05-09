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
