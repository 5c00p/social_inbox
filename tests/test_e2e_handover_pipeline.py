"""E2E tests: safety net — symptom pre-empts Claude, operator gets polite ack,
outgoing banned pattern triggers handover with audit trail.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import AsyncClient

from app.models.events import IncomingEvent
from app.repos import events as events_repo
from app.repos import users
from app.services import claude_responder, lead_tracker, notifications
from app.workers.tasks_messages import process_incoming_event
from tests.fakes.fake_provider import FakeProvider


async def test_symptom_message_triggers_handover_without_claude(
    client: AsyncClient,
    fake_provider: FakeProvider,
    db: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Symptom keyword in DM → pre-emptive handover, Claude never called."""
    notify_mock = AsyncMock(return_value=True)
    monkeypatch.setattr(notifications, "notify_admin", notify_mock)

    # If Claude is called, fail loudly — symptom should pre-empt
    fake_anthropic = MagicMock()
    fake_anthropic.messages.create = AsyncMock(
        side_effect=AssertionError("Claude must NOT be called for symptom messages"),
    )
    monkeypatch.setattr(claude_responder, "_client", fake_anthropic)

    event = IncomingEvent(
        provider="sendpulse",
        platform="instagram",
        event_type="message",
        external_user_id="symptom_user",
        external_event_id="symp_evt_1",
        username="anna",
        full_name="Anna P",
        text="у меня болит голова, что использовать?",
        occurred_at=datetime(2026, 5, 8, 12, 0, tzinfo=UTC),
    )
    log_row = await events_repo.insert(
        provider_name=event.provider, platform=event.platform,
        event_type=event.event_type, external_event_id=event.external_event_id,
        payload={}, signature_valid=True,
    )

    await process_incoming_event({}, event.model_dump(mode="json"), log_row["id"])

    # Conversation flipped
    user = await users.get_by_external("sendpulse", "instagram", "symptom_user")
    assert user is not None
    conv = await db.fetchrow(
        "SELECT * FROM conversations WHERE user_id = $1", user["id"],
    )
    assert conv["status"] == "handover_pending"
    assert "symptom_detected" in (conv["handover_reason"] or "")

    # No outgoing message sent
    assert len(fake_provider.sent) == 0

    # Admin notified
    notify_mock.assert_called_once()


async def test_operator_keyword_routes_through_handover_scenario(
    client: AsyncClient,
    fake_provider: FakeProvider,
    db: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """operator keyword → engine matches keyword → handover scenario → polite ack sent."""
    monkeypatch.setattr(notifications, "notify_admin", AsyncMock(return_value=True))

    event = IncomingEvent(
        provider="sendpulse",
        platform="instagram",
        event_type="message",
        external_user_id="operator_user",
        external_event_id="op_evt_1",
        full_name="Anna",
        text="хочу оператора, у меня вопрос",
        occurred_at=datetime(2026, 5, 8, 12, 0, tzinfo=UTC),
    )
    log_row = await events_repo.insert(
        provider_name=event.provider, platform=event.platform,
        event_type=event.event_type, external_event_id=event.external_event_id,
        payload={}, signature_valid=True,
    )

    await process_incoming_event({}, event.model_dump(mode="json"), log_row["id"])

    # Polite ack sent to user
    assert len(fake_provider.sent) == 1
    assert "Юле" in fake_provider.sent[0].text

    # Status flipped
    user = await users.get_by_external("sendpulse", "instagram", "operator_user")
    assert user is not None
    conv = await db.fetchrow(
        "SELECT * FROM conversations WHERE user_id = $1", user["id"],
    )
    assert conv["status"] == "handover_pending"


async def test_outgoing_safety_blocks_claude_with_medical_claim(
    client: AsyncClient,
    fake_provider: FakeProvider,
    db: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Claude returns medical claim → safety blocks → no message sent + handover + audit."""
    monkeypatch.setattr(notifications, "notify_admin", AsyncMock(return_value=True))

    # Pre-create user (returning → smart fallback fires)
    user = await users.create(
        provider_name="sendpulse", platform="instagram",
        external_id="safety_block_user", full_name="Anna",
    )
    await lead_tracker.mark_welcome_sent(user["id"])

    # Stub Claude to return a medical claim
    @dataclass
    class _Usage:
        input_tokens: int = 50
        output_tokens: int = 30

    @dataclass
    class _Text:
        type: str = "text"
        text: str = ""

    @dataclass
    class _Resp:
        content: list[Any] = field(default_factory=list)
        usage: _Usage = field(default_factory=_Usage)

    bad_response = _Resp(
        content=[_Text(text="Конечно! Это масло вылечит ваш недуг 🌿")],
        usage=_Usage(),
    )

    fake_anthropic = MagicMock()
    fake_anthropic.messages.create = AsyncMock(return_value=bad_response)
    monkeypatch.setattr(claude_responder, "_client", fake_anthropic)

    event = IncomingEvent(
        provider="sendpulse",
        platform="instagram",
        event_type="message",
        external_user_id="safety_block_user",
        external_event_id="sb_evt_1",
        text="расскажи про масла",  # benign — passes incoming check
        occurred_at=datetime(2026, 5, 8, 12, 0, tzinfo=UTC),
    )
    log_row = await events_repo.insert(
        provider_name=event.provider, platform=event.platform,
        event_type=event.event_type, external_event_id=event.external_event_id,
        payload={}, signature_valid=True,
    )

    await process_incoming_event({}, event.model_dump(mode="json"), log_row["id"])

    # Nothing sent to user
    assert len(fake_provider.sent) == 0

    # Audit row exists with safety_blocked=True
    blocked_msg = await db.fetchrow(
        """
        SELECT * FROM messages
        WHERE conversation_id IN (SELECT id FROM conversations WHERE user_id = $1)
          AND safety_blocked = TRUE
        """,
        user["id"],
    )
    assert blocked_msg is not None
    assert "вылечит" in (blocked_msg["safety_reason"] or "")
    assert blocked_msg["text"] is None  # never delivered

    # Conversation in handover
    conv = await db.fetchrow(
        "SELECT * FROM conversations WHERE user_id = $1", user["id"],
    )
    assert conv["status"] == "handover_pending"
    assert "outgoing_safety_block" in (conv["handover_reason"] or "")
