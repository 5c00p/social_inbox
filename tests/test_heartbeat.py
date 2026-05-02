"""Tests for worker heartbeat."""
from __future__ import annotations

import pytest

from app.workers.heartbeat import (
    HEARTBEAT_KEY,
    heartbeat_age_seconds,
    heartbeat_tick,
)


@pytest.mark.asyncio
async def test_heartbeat_tick_writes_to_redis() -> None:
    from app.repos.redis_client import get_redis

    await heartbeat_tick()
    redis = await get_redis()
    value = await redis.get(HEARTBEAT_KEY)
    assert value is not None


@pytest.mark.asyncio
async def test_heartbeat_age_returns_small_value_after_tick() -> None:
    await heartbeat_tick()
    age = await heartbeat_age_seconds()
    assert age is not None
    assert age < 5.0  # should be near-zero, but allow CI slack


@pytest.mark.asyncio
async def test_heartbeat_age_returns_none_when_missing() -> None:
    from app.repos.redis_client import get_redis

    redis = await get_redis()
    await redis.delete(HEARTBEAT_KEY)
    age = await heartbeat_age_seconds()
    assert age is None
