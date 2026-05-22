"""Polling cursor for SendPulse — tracks the last-fetched timestamp per bot.

Why per-bot: in theory we could have multiple SendPulse bots later;
keying by bot_id avoids collision.

Format in Redis:
    sendpulse:cursor:<bot_id> → ISO datetime string

On first run (no cursor), we start from NOW() - 5 minutes to avoid
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
