"""Worker liveness heartbeat written to Redis.

Healthchecks (in Task 16) read the key and confirm it's recent.
"""
from __future__ import annotations

from datetime import UTC, datetime

from app.repos.redis_client import get_redis
from app.utils.logging import get_logger

log = get_logger(__name__)

HEARTBEAT_KEY = "worker:heartbeat"
HEARTBEAT_TTL_SECONDS = 180  # if missing for >3 min, worker is unhealthy


async def heartbeat_tick() -> None:
    """Write current UTC timestamp to Redis."""
    redis = await get_redis()
    now = datetime.now(UTC).isoformat()
    await redis.set(HEARTBEAT_KEY, now, ex=HEARTBEAT_TTL_SECONDS)


async def heartbeat_age_seconds() -> float | None:
    """Return seconds since last heartbeat, or None if missing."""
    redis = await get_redis()
    value = await redis.get(HEARTBEAT_KEY)
    if not value:
        return None
    try:
        ts = datetime.fromisoformat(value)
    except ValueError:
        return None
    return (datetime.now(UTC) - ts).total_seconds()
