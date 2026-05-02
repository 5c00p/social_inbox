"""Token-bucket-style rate limiter on Redis.

For Task 07 we use a simple sliding-window via INCR + EXPIRE:
- Each (user, action) pair has a counter in Redis
- Counter expires after `window_seconds`
- If counter exceeds `limit`, deny

Limits configured here (intentionally simple for MVP):
- replies_per_user_per_minute: 5

Future limits (not in this task — see CLAUDE.md §12.4):
- 10 replies per user per day
- 1 welcome per user lifetime
- 1 comment-to-DM per (user, scenario) per 30 days
These will be implemented in Tasks 08/09 with their own keys.
"""
from __future__ import annotations

from app.repos.redis_client import get_redis
from app.utils.logging import get_logger

log = get_logger(__name__)

# Defaults
REPLIES_PER_MINUTE_LIMIT = 5
REPLIES_PER_MINUTE_WINDOW = 60


async def check_and_increment(
    key: str,
    limit: int,
    window_seconds: int,
) -> bool:
    """Atomically increment counter; return True if within limit, False if over.

    Uses INCR (creates key if missing) and sets EXPIRE only on first hit.
    This is the standard sliding-window approximation and good enough for our scale.
    """
    redis = await get_redis()
    count = int(await redis.incr(key))
    if count == 1:
        await redis.expire(key, window_seconds)
    return count <= limit


async def can_reply(user_id: int) -> bool:
    """Returns True if we can reply to this user right now.

    Checks the per-minute reply limit. If over — caller should NOT send the message
    and should log the throttle.
    """
    key = f"rl:reply:{user_id}"
    allowed = await check_and_increment(
        key, REPLIES_PER_MINUTE_LIMIT, REPLIES_PER_MINUTE_WINDOW,
    )
    if not allowed:
        log.warning("rate_limit_hit_replies_per_minute", user_id=user_id)
    return allowed
