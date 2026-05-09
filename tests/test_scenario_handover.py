"""Tests for handover scenario handler (keyword 'оператор')."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.models.events import IncomingEvent
from app.repos import conversations, users
from app.repos import scenarios as scenarios_repo
from app.services import notifications
from app.services.scenarios.handover import handle_handover


async def test_handover_returns_polite_ack(
    db: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(notifications, "notify_admin", AsyncMock(return_value=True))

    user = await users.create(
        provider_name="sendpulse", platform="instagram",
        external_id="hsc_user_1",
    )
    conv = await conversations.create(user["id"], "instagram")
    scenario = await scenarios_repo.get_by_name("default_handover")
    assert scenario is not None, "default_handover must be seeded by conftest"

    event = IncomingEvent(
        provider="sendpulse",
        platform="instagram",
        event_type="message",
        external_user_id="hsc_user_1",
        external_event_id="evt_h_1",
        text="Хочу к оператору",
        occurred_at=datetime.now(UTC),
    )

    msg = await handle_handover(event, user, conv, scenario)

    assert msg is not None
    assert msg.text is not None
    assert "Юле" in msg.text or "юле" in msg.text.lower()

    updated = await db.fetchrow(
        "SELECT status FROM conversations WHERE id = $1", conv["id"],
    )
    assert updated["status"] == "handover_pending"
