"""Tests for RateLimiter."""
from __future__ import annotations

import pytest

from app.repos.redis_client import get_redis
from app.services.rate_limiter import (
    REPLIES_PER_MINUTE_LIMIT,
    can_reply,
    check_and_increment,
)


@pytest.mark.asyncio
async def test_check_and_increment_allows_under_limit() -> None:
    redis = await get_redis()
    await redis.delete("rl:test:1")
    for _ in range(3):
        ok = await check_and_increment("rl:test:1", limit=5, window_seconds=60)
        assert ok is True


@pytest.mark.asyncio
async def test_check_and_increment_blocks_over_limit() -> None:
    redis = await get_redis()
    await redis.delete("rl:test:2")
    for _ in range(5):
        await check_and_increment("rl:test:2", limit=5, window_seconds=60)
    # Sixth call: blocked
    ok = await check_and_increment("rl:test:2", limit=5, window_seconds=60)
    assert ok is False


@pytest.mark.asyncio
async def test_can_reply_uses_per_user_key() -> None:
    redis = await get_redis()
    user_id = 9999
    await redis.delete(f"rl:reply:{user_id}")

    for _ in range(REPLIES_PER_MINUTE_LIMIT):
        ok = await can_reply(user_id)
        assert ok is True
    # Next one is over the limit
    assert await can_reply(user_id) is False
