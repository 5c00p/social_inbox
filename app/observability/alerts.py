"""Operational alerts to admin via Telegram, with deduplication.

Why dedup: if worker is dead for an hour, we don't want 60 alerts.
Pattern: each alert type has a TTL window. Within the window, repeated
fires are suppressed. After TTL expires, alert fires again (so admin
knows the issue is ongoing).

Alert types and their TTLs:
- worker_dead              30 min  — heartbeat stale
- claude_failures          15 min  — Claude API errors above threshold
- postgres_down             5 min  — DB unreachable
- webhook_parse_failures   30 min  — too many parse errors from provider
"""
from __future__ import annotations

from typing import Literal

from app.repos.redis_client import get_redis
from app.services import notifications
from app.utils.logging import get_logger

log = get_logger(__name__)

AlertType = Literal[
    "worker_dead",
    "claude_failures",
    "postgres_down",
    "webhook_parse_failures",
]

DEDUP_TTL_BY_TYPE: dict[AlertType, int] = {
    "worker_dead": 30 * 60,
    "claude_failures": 15 * 60,
    "postgres_down": 5 * 60,
    "webhook_parse_failures": 30 * 60,
}


def _dedup_key(alert_type: AlertType) -> str:
    return f"alert:dedup:{alert_type}"


async def fire_alert(
    alert_type: AlertType,
    message: str,
) -> bool:
    """Send an alert to admin if not already fired within the dedup window.

    Returns True if alert was sent, False if suppressed by dedup.
    """
    redis = await get_redis()
    key = _dedup_key(alert_type)
    ttl = DEDUP_TTL_BY_TYPE[alert_type]

    was_set = await redis.set(key, "1", nx=True, ex=ttl)
    if not was_set:
        log.debug("alert_suppressed_dedup", type=alert_type)
        return False

    full_message = f"🚨 *{alert_type}*\n\n{message}"
    await notifications.notify_admin(full_message)
    log.warning("alert_fired", type=alert_type, message=message[:200])
    return True


async def reset_dedup(alert_type: AlertType) -> None:
    """Manually reset a dedup window.

    Useful when issue is acknowledged so the admin can be re-notified
    if it recurs immediately.
    """
    redis = await get_redis()
    await redis.delete(_dedup_key(alert_type))
