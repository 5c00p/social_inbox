"""Singleton Redis client.

Used by:
- arq queue (job enqueue/dequeue)
- worker heartbeat
- rate limiter (Task 14)
- token cache for SendPulse OAuth (Task 05)
"""
from __future__ import annotations

from redis.asyncio import Redis, from_url

from app.config import get_settings
from app.utils.logging import get_logger

log = get_logger(__name__)

_client: Redis | None = None


async def get_redis() -> Redis:
    """Return the global Redis client, creating it on first call."""
    global _client
    if _client is None:
        settings = get_settings()
        _client = from_url(  # type: ignore[no-untyped-call]
            settings.redis_url,
            encoding="utf-8",
            decode_responses=True,
            health_check_interval=30,
        )
        log.info("redis_client_created")
    return _client


async def close_redis() -> None:
    """Close the Redis client. Call on shutdown."""
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None
        log.info("redis_client_closed")


async def ping() -> bool:
    """Return True if Redis is reachable. Used by /ready endpoint."""
    try:
        client = await get_redis()
        return bool(await client.ping())
    except (ConnectionError, OSError) as exc:
        log.warning("redis_ping_failed", error=str(exc))
        return False
