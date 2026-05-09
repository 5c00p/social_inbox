"""Tests for smart scenario handler."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.models.events import IncomingEvent
from app.repos import conversations, users
from app.repos import scenarios as scenarios_repo
from app.services import claude_responder
from app.services.claude_responder import ClaudeReply
from app.services.scenarios.smart import handle_smart


def _event(text: str = "А что входит в программу?") -> IncomingEvent:
    return IncomingEvent(
        provider="sendpulse",
        platform="instagram",
        event_type="message",
        external_user_id="smart_user_e",
        external_event_id="evt_s_1",
        text=text,
        occurred_at=datetime(2026, 5, 8, 12, 0, tzinfo=UTC),
    )


async def _setup(db: Any, external_id: str = "smart_user") -> tuple[Any, Any, Any]:
    user = await users.create(
        provider_name="sendpulse",
        platform="instagram",
        external_id=external_id,
    )
    conv = await conversations.create(user["id"], "instagram")
    scenario = await scenarios_repo.get_by_name("default_smart")
    assert scenario is not None, "default_smart must be seeded by conftest"
    return user, conv, scenario


async def test_smart_returns_outgoing_message(db: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    user, conv, scenario = await _setup(db, "smart_ok")
    monkeypatch.setattr(
        claude_responder,
        "respond",
        AsyncMock(return_value=ClaudeReply(
            text="Программа включает 30 дней с маслами 🌿",
            escalation=False,
            escalation_reason=None,
            tokens_in=120,
            tokens_out=30,
            model="claude-sonnet-4-6",
        )),
    )

    msg = await handle_smart(_event(), user, conv, scenario)

    assert msg is not None
    assert "30 дней" in msg.text
    assert msg.scenario_id == scenario["id"]
    assert msg.claude_metadata == {
        "model": "claude-sonnet-4-6",
        "tokens_in": 120,
        "tokens_out": 30,
    }


async def test_smart_escalation_sets_handover(db: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    user, conv, scenario = await _setup(db, "smart_esc")
    monkeypatch.setattr(
        claude_responder,
        "respond",
        AsyncMock(return_value=ClaudeReply(
            text=None,
            escalation=True,
            escalation_reason="Симптомы — нужен врач",
            tokens_in=80,
            tokens_out=15,
            model="claude-sonnet-4-6",
        )),
    )

    msg = await handle_smart(_event(text="у меня болит голова"), user, conv, scenario)

    assert msg is None  # no reply sent

    updated = await db.fetchrow("SELECT * FROM conversations WHERE id = $1", conv["id"])
    assert updated["status"] == "handover_pending"
    assert "Симптомы" in (updated["handover_reason"] or "")


async def test_smart_skipped_when_smart_mode_disabled(
    db: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    user, conv, scenario = await _setup(db, "smart_disabled")
    await db.execute(
        "UPDATE social_users SET smart_mode_enabled = FALSE WHERE id = $1",
        user["id"],
    )
    user_disabled = await db.fetchrow(
        "SELECT * FROM social_users WHERE id = $1", user["id"],
    )

    respond_mock = AsyncMock()
    monkeypatch.setattr(claude_responder, "respond", respond_mock)

    msg = await handle_smart(_event(), user_disabled, conv, scenario)
    assert msg is None
    respond_mock.assert_not_called()


async def test_smart_skipped_on_empty_text(db: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    user, conv, scenario = await _setup(db, "smart_empty")
    respond_mock = AsyncMock()
    monkeypatch.setattr(claude_responder, "respond", respond_mock)

    event_no_text = _event(text="")
    msg = await handle_smart(event_no_text, user, conv, scenario)

    assert msg is None
    respond_mock.assert_not_called()


async def test_smart_returns_none_when_responder_returns_none(
    db: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    user, conv, scenario = await _setup(db, "smart_none")
    monkeypatch.setattr(
        claude_responder,
        "respond",
        AsyncMock(return_value=None),
    )

    msg = await handle_smart(_event(), user, conv, scenario)
    assert msg is None
