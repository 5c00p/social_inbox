"""Tests for alert deduplication."""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.observability import alerts
from app.repos.redis_client import get_redis
from app.services import notifications


@pytest.fixture(autouse=True)
async def _clear_dedup_keys() -> None:  # type: ignore[misc]
    redis = await get_redis()
    keys = await redis.keys("alert:dedup:*")
    if keys:
        await redis.delete(*keys)
    yield
    keys = await redis.keys("alert:dedup:*")
    if keys:
        await redis.delete(*keys)


@pytest.mark.usefixtures("_db_setup")
async def test_first_alert_fires(monkeypatch: pytest.MonkeyPatch) -> None:
    notify_mock = AsyncMock(return_value=True)
    monkeypatch.setattr(notifications, "notify_admin", notify_mock)

    fired = await alerts.fire_alert("worker_dead", "test message")
    assert fired is True
    notify_mock.assert_called_once()


@pytest.mark.usefixtures("_db_setup")
async def test_duplicate_alert_suppressed(monkeypatch: pytest.MonkeyPatch) -> None:
    notify_mock = AsyncMock(return_value=True)
    monkeypatch.setattr(notifications, "notify_admin", notify_mock)

    await alerts.fire_alert("worker_dead", "first")
    fired_again = await alerts.fire_alert("worker_dead", "second within window")

    assert fired_again is False
    notify_mock.assert_called_once()


@pytest.mark.usefixtures("_db_setup")
async def test_different_alert_types_independent(monkeypatch: pytest.MonkeyPatch) -> None:
    notify_mock = AsyncMock(return_value=True)
    monkeypatch.setattr(notifications, "notify_admin", notify_mock)

    await alerts.fire_alert("worker_dead", "x")
    await alerts.fire_alert("postgres_down", "y")

    assert notify_mock.call_count == 2


@pytest.mark.usefixtures("_db_setup")
async def test_reset_dedup_allows_refire(monkeypatch: pytest.MonkeyPatch) -> None:
    notify_mock = AsyncMock(return_value=True)
    monkeypatch.setattr(notifications, "notify_admin", notify_mock)

    await alerts.fire_alert("worker_dead", "first")
    await alerts.reset_dedup("worker_dead")
    fired = await alerts.fire_alert("worker_dead", "after reset")

    assert fired is True
    assert notify_mock.call_count == 2
