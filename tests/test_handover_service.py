"""Tests for handover service: status flip + admin notification."""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.repos import conversations, users
from app.services import handover, notifications


async def test_trigger_handover_flips_status(
    db: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(notifications, "notify_admin", AsyncMock(return_value=True))

    user = await users.create(
        provider_name="sendpulse", platform="instagram",
        external_id="ho_user_1", username="anna", full_name="Anna",
    )
    conv = await conversations.create(user["id"], "instagram")

    await handover.trigger_handover(
        conversation=conv, user=user,
        source="operator_request",
        reason="user typed 'оператор'",
    )

    updated = await db.fetchrow(
        "SELECT status, handover_reason FROM conversations WHERE id = $1",
        conv["id"],
    )
    assert updated["status"] == "handover_pending"
    assert "operator_request" in updated["handover_reason"]


async def test_trigger_handover_calls_notify_admin(
    db: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    notify_mock = AsyncMock(return_value=True)
    monkeypatch.setattr(notifications, "notify_admin", notify_mock)

    user = await users.create(
        provider_name="sendpulse", platform="instagram",
        external_id="ho_notify", username="bob", full_name="Bob Smith",
    )
    conv = await conversations.create(user["id"], "instagram")

    await handover.trigger_handover(
        conversation=conv, user=user,
        source="symptom_detected",
        reason="болит",
    )

    notify_mock.assert_called_once()
    sent_text = notify_mock.call_args.args[0]
    assert "Симптомы" in sent_text or "симптом" in sent_text.lower()
    assert "bob" in sent_text
    assert user["short_id"] in sent_text


async def test_trigger_handover_idempotent(
    db: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(notifications, "notify_admin", AsyncMock(return_value=True))

    user = await users.create(
        provider_name="sendpulse", platform="instagram",
        external_id="ho_idem",
    )
    conv = await conversations.create(user["id"], "instagram")

    await handover.trigger_handover(
        conversation=conv, user=user, source="operator_request", reason="r1",
    )
    # Second call doesn't crash
    await handover.trigger_handover(
        conversation=conv, user=user, source="operator_request", reason="r2",
    )

    updated = await db.fetchrow(
        "SELECT handover_reason FROM conversations WHERE id = $1",
        conv["id"],
    )
    # Latest reason wins
    assert "r2" in updated["handover_reason"]
