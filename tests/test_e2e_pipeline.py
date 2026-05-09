"""End-to-end test: webhook → DB → scenario → reply → DB.

Tests the full pipeline for a returning user (welcome already sent), which
triggers the smart fallback scenario. New-user welcome flow is covered by
test_e2e_welcome_pipeline.py.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import AsyncClient

from app.models.events import IncomingEvent
from app.repos import users
from app.services import claude_responder, lead_tracker
from app.workers.tasks_messages import process_incoming_event
from tests.fakes.fake_provider import FakeProvider


@dataclass
class _FakeUsage:
    input_tokens: int
    output_tokens: int


@dataclass
class _FakeContentText:
    type: str = "text"
    text: str = ""


@dataclass
class _FakeResponse:
    content: list[Any]
    usage: _FakeUsage


@pytest.mark.asyncio
async def test_full_pipeline_smart(
    client: AsyncClient,
    fake_provider: FakeProvider,
    db,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Webhook → events_log → worker → smart scenario → Claude → provider.send → outgoing message in DB.

    Uses a pre-existing user with welcome already sent so the engine routes to smart.
    """
    # Mock Anthropic so no real API call is made
    fake_client = MagicMock()
    fake_messages = MagicMock()
    fake_messages.create = AsyncMock(return_value=_FakeResponse(
        content=[_FakeContentText(text="Отличный вопрос! 🌿")],
        usage=_FakeUsage(input_tokens=80, output_tokens=15),
    ))
    fake_client.messages = fake_messages
    monkeypatch.setattr(claude_responder, "_client", fake_client)

    # Pre-create user so is_new_user=False and engine falls through to smart.
    user = await users.create(
        provider_name="sendpulse",
        platform="instagram",
        external_id="e2e_user_1",
        username="e2e_user",
    )
    await lead_tracker.mark_welcome_sent(user["id"])

    event = IncomingEvent(
        provider="sendpulse",
        platform="instagram",
        event_type="message",
        external_user_id="e2e_user_1",
        external_event_id="e2e_evt_1",
        username="e2e_user",
        text="привет",
        occurred_at=datetime(2026, 4, 30, 12, 0, tzinfo=UTC),
    )
    fake_provider.queue_event(event)

    # 1. POST webhook → records to events_log AND enqueues via arq.
    # Since we don't run arq runtime in tests, manually drive the worker step.
    response = await client.post("/webhooks/sendpulse", json={})
    assert response.status_code == 200

    log_row = await db.fetchrow(
        "SELECT * FROM events_log WHERE external_event_id = $1",
        "e2e_evt_1",
    )
    assert log_row is not None
    assert log_row["processed_at"] is None  # worker hasn't run yet

    # 2. Drive the worker manually
    await process_incoming_event({}, event.model_dump(mode="json"), log_row["id"])

    # 3. Verify processed
    log_after = await db.fetchrow(
        "SELECT * FROM events_log WHERE id = $1", log_row["id"],
    )
    assert log_after["processed_at"] is not None
    assert log_after["error"] is None

    # 4. User still exists
    user = await users.get_by_external("sendpulse", "instagram", "e2e_user_1")
    assert user is not None

    # 5. Two messages: incoming and outgoing smart reply
    msgs = await db.fetch(
        """
        SELECT m.* FROM messages m
        JOIN conversations c ON c.id = m.conversation_id
        WHERE c.user_id = $1
        ORDER BY m.created_at ASC
        """,
        user["id"],
    )
    assert len(msgs) == 2
    assert msgs[0]["direction"] == "in"
    assert msgs[0]["text"] == "привет"
    assert msgs[1]["direction"] == "out"
    assert msgs[1]["text"] is not None
    assert msgs[1]["scenario_id"] is not None
    assert msgs[1]["claude_used"] is True

    # 6. Provider received the outgoing message
    assert len(fake_provider.sent) == 1
    sent = fake_provider.sent[0]
    assert sent.platform == "instagram"
    assert sent.external_user_id == "e2e_user_1"
