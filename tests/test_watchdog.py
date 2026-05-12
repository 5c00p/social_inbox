"""Tests for watchdog_check task."""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.repos.redis_client import get_redis
from app.services import notifications
from app.workers import tasks_watchdog
from app.workers.heartbeat import HEARTBEAT_KEY, heartbeat_tick


@pytest.fixture(autouse=True)
async def _clear_dedup() -> None:  # type: ignore[misc]
    redis = await get_redis()
    keys = await redis.keys("alert:dedup:*")
    if keys:
        await redis.delete(*keys)
    yield


@pytest.mark.usefixtures("_db_setup")
async def test_watchdog_alerts_on_stale_heartbeat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    notify_mock = AsyncMock(return_value=True)
    monkeypatch.setattr(notifications, "notify_admin", notify_mock)

    redis = await get_redis()
    await redis.delete(HEARTBEAT_KEY)

    await tasks_watchdog.watchdog_check({})

    # worker_dead alert fired
    assert any(
        "worker_dead" in str(call.args[0])
        for call in notify_mock.call_args_list
    )


@pytest.mark.usefixtures("_db_setup")
async def test_watchdog_silent_when_healthy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    notify_mock = AsyncMock(return_value=True)
    monkeypatch.setattr(notifications, "notify_admin", notify_mock)

    await heartbeat_tick()  # fresh heartbeat

    await tasks_watchdog.watchdog_check({})

    notify_mock.assert_not_called()
