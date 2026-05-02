"""End-to-end test: webhook → DB → scenario → reply → DB.

Tests the full pipeline for a returning user (welcome already sent), which
triggers the echo fallback scenario. New-user welcome flow is covered by
test_e2e_welcome_pipeline.py.
"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest
from httpx import AsyncClient

from app.models.events import IncomingEvent
from app.repos import users
from app.services import lead_tracker
from app.workers.tasks_messages import process_incoming_event
from tests.fakes.fake_provider import FakeProvider


@pytest.mark.asyncio
async def test_full_pipeline_echo(
    client: AsyncClient,
    fake_provider: FakeProvider,
    db,
) -> None:
    """Webhook → events_log → worker → echo scenario → provider.send → outgoing message in DB.

    Uses a pre-existing user with welcome already sent so the engine routes to echo.
    """
    # Pre-create user so is_new_user=False and engine falls through to echo.
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

    # 5. Two messages: incoming and outgoing echo
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
    assert "Получено:" in msgs[1]["text"]
    assert msgs[1]["scenario_id"] is not None

    # 6. Provider received the outgoing message
    assert len(fake_provider.sent) == 1
    sent = fake_provider.sent[0]
    assert sent.platform == "instagram"
    assert sent.external_user_id == "e2e_user_1"
    assert "Получено:" in (sent.text or "")
