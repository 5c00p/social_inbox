"""Tests for daily digest task."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from app.repos import users
from app.services import notifications
from app.workers.tasks_watchdog import daily_digest


@pytest.mark.usefixtures("_db_setup")
async def test_daily_digest_sends_admin_message(
    db: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    notify_mock = AsyncMock(return_value=True)
    monkeypatch.setattr(notifications, "notify_admin", notify_mock)

    # Seed a user "yesterday"
    yesterday = datetime.now(UTC) - timedelta(days=1, hours=2)
    user = await users.create(
        provider_name="sendpulse", platform="instagram",
        external_id="digest_user_1",
    )
    from app.repos.pool import get_pool
    pool = await get_pool()
    await pool.execute(
        "UPDATE social_users SET first_seen_at = $2 WHERE id = $1",
        user["id"], yesterday,
    )

    await daily_digest({})

    notify_mock.assert_called_once()
    text = notify_mock.call_args.args[0]
    assert "Сводка" in text
    assert "Новых лидов" in text


@pytest.mark.usefixtures("_db_setup")
async def test_daily_digest_zero_when_no_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    notify_mock = AsyncMock(return_value=True)
    monkeypatch.setattr(notifications, "notify_admin", notify_mock)

    await daily_digest({})

    notify_mock.assert_called_once()
    text = notify_mock.call_args.args[0]
    # Even with no data, message structure is correct
    assert "Сводка" in text
    assert "*0*" in text  # zeros for fields
