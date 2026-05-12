"""Tests for Claude failure rate tracking."""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.observability import claude_health
from app.repos.redis_client import get_redis
from app.services import notifications


@pytest.fixture(autouse=True)
async def _clear_health_keys() -> None:  # type: ignore[misc]
    redis = await get_redis()
    keys = await redis.keys("claude:health:*")
    keys.extend(await redis.keys("alert:dedup:*"))
    if keys:
        await redis.delete(*keys)
    yield
    keys = await redis.keys("claude:health:*")
    keys.extend(await redis.keys("alert:dedup:*"))
    if keys:
        await redis.delete(*keys)


@pytest.mark.usefixtures("_db_setup")
async def test_no_alert_below_min_attempts(monkeypatch: pytest.MonkeyPatch) -> None:
    notify_mock = AsyncMock(return_value=True)
    monkeypatch.setattr(notifications, "notify_admin", notify_mock)

    # 4 failures — below MIN_ATTEMPTS (5)
    for _ in range(4):
        await claude_health.record_failure()

    notify_mock.assert_not_called()


@pytest.mark.usefixtures("_db_setup")
async def test_alert_fires_above_threshold(monkeypatch: pytest.MonkeyPatch) -> None:
    notify_mock = AsyncMock(return_value=True)
    monkeypatch.setattr(notifications, "notify_admin", notify_mock)

    # 5 failures, 0 successes → 100% failure rate
    for _ in range(5):
        await claude_health.record_failure()

    notify_mock.assert_called_once()
    call_text = notify_mock.call_args.args[0]
    assert "claude_failures" in call_text


@pytest.mark.usefixtures("_db_setup")
async def test_no_alert_when_mostly_successes(monkeypatch: pytest.MonkeyPatch) -> None:
    notify_mock = AsyncMock(return_value=True)
    monkeypatch.setattr(notifications, "notify_admin", notify_mock)

    # 9 successes, 1 failure = 10% rate, below threshold
    for _ in range(9):
        await claude_health.record_success()
    await claude_health.record_failure()

    notify_mock.assert_not_called()
