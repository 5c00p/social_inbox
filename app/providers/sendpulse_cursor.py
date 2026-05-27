"""Polling cursor for SendPulse — tracks the last-fetched timestamp per bot.

Why per-bot: in theory we could have multiple SendPulse bots later;
keying by bot_id avoids collision.

Format in Redis:
    sendpulse:cursor:<bot_id> -> ISO datetime string
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.repos.redis_client import get_redis


def _key(bot_id: str) -> str:
    return f"sendpulse:cursor:{bot_id}"


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
    redis = await get_redis()
    raw = await redis.get(_key(bot_id))
    if raw:
        try:
            return datetime.fromisoformat(raw)
        except ValueError:
            pass
    return datetime.now(UTC)


async def set_cursor(bot_id: str, ts: datetime) -> None:
    """Persist the latest polled timestamp."""
    redis = await get_redis()
    await redis.set(_key(bot_id), ts.isoformat())
