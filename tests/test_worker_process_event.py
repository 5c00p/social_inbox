"""Tests for worker task: process_incoming_event.

These tests call the task function directly (no arq runtime needed).
"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.models.events import IncomingEvent
from app.repos import conversations, users
from app.repos import events as events_repo
from app.workers.tasks_messages import process_incoming_event


def _event(
    external_event_id: str = "evt_w_1",
    external_user_id: str = "ig_user_1",
    text: str = "Hi",
) -> IncomingEvent:
    return IncomingEvent(
        provider="sendpulse",
        platform="instagram",
        event_type="message",
        external_user_id=external_user_id,
        external_event_id=external_event_id,
        username="bob",
        full_name="Bob Smith",
        text=text,
        occurred_at=datetime(2026, 4, 30, 12, 0, tzinfo=UTC),
    )


@pytest.mark.asyncio
async def test_process_event_creates_user_conversation_message(db) -> None:
    # Setup: insert event_log row first (webhook handler does this in production)
    event = _event(external_event_id="evt_e2e_1")
    log_row = await events_repo.insert(
        provider_name=event.provider,
        platform=event.platform,
        event_type=event.event_type,
        external_event_id=event.external_event_id,
        payload={},
        signature_valid=True,
    )

    # Act
    await process_incoming_event({}, event.model_dump(mode="json"), log_row["id"])

    # Assert: user created
    user = await users.get_by_external("sendpulse", "instagram", "ig_user_1")
    assert user is not None
    assert user["username"] == "bob"

    # Assert: conversation created
    conv = await conversations.get_active(user["id"], "instagram")
    assert conv is not None

    # Assert: message inserted
    row = await db.fetchrow(
        "SELECT * FROM messages WHERE conversation_id = $1",
        conv["id"],
    )
    assert row is not None
    assert row["direction"] == "in"
    assert row["text"] == "Hi"
    assert row["source"] == "dm"

    # Assert: events_log marked processed
    log_after = await db.fetchrow(
        "SELECT processed_at, error FROM events_log WHERE id = $1",
        log_row["id"],
    )
    assert log_after["processed_at"] is not None
    assert log_after["error"] is None


@pytest.mark.asyncio
async def test_process_event_idempotent_via_events_log(db) -> None:
    """Replaying the same event_id is a no-op."""
    event = _event(external_event_id="evt_idem_1")
    log_row = await events_repo.insert(
        provider_name=event.provider,
        platform=event.platform,
        event_type=event.event_type,
        external_event_id=event.external_event_id,
        payload={},
        signature_valid=True,
    )

    # First processing
    await process_incoming_event({}, event.model_dump(mode="json"), log_row["id"])

    # Second processing — should skip
    await process_incoming_event({}, event.model_dump(mode="json"), log_row["id"])

    # Exactly 2 messages (1 in + 1 out echo) — NOT 4, proving the second call was a no-op.
    user = await users.get_by_external("sendpulse", "instagram", event.external_user_id)
    assert user is not None
    conv = await conversations.get_active(user["id"], "instagram")
    rows = await db.fetch(
        "SELECT direction FROM messages WHERE conversation_id = $1 ORDER BY id",
        conv["id"],
    )
    assert len(rows) == 2
    assert rows[0]["direction"] == "in"
    assert rows[1]["direction"] == "out"


@pytest.mark.asyncio
async def test_process_event_for_existing_user(db) -> None:
    """If user already exists, do not create duplicate."""
    # Pre-create user
    await users.create(
        provider_name="sendpulse",
        platform="instagram",
        external_id="existing_user",
        username="old_username",
    )

    event = _event(
        external_event_id="evt_existing_1",
        external_user_id="existing_user",
    )
    log_row = await events_repo.insert(
        provider_name=event.provider,
        platform=event.platform,
        event_type=event.event_type,
        external_event_id=event.external_event_id,
        payload={},
        signature_valid=True,
    )
    await process_incoming_event({}, event.model_dump(mode="json"), log_row["id"])

    # Still exactly one user
    rows = await db.fetch(
        "SELECT * FROM social_users WHERE external_id = $1",
        "existing_user",
    )
    assert len(rows) == 1
    # Username NOT updated (process_incoming_event doesn't update existing users in Task 06)
    assert rows[0]["username"] == "old_username"


@pytest.mark.asyncio
async def test_process_event_comment_type(db) -> None:
    """Event with event_type='comment' produces source='comment'."""
    event = IncomingEvent(
        provider="sendpulse",
        platform="instagram",
        event_type="comment",
        external_user_id="commenter_1",
        external_event_id="evt_c_1",
        post_id="post_42",
        comment_id="comment_99",
        text="ОЧИЩЕНИЕ",
        occurred_at=datetime.now(UTC),
    )
    log_row = await events_repo.insert(
        provider_name="sendpulse",
        platform="instagram",
        event_type="comment",
        external_event_id="evt_c_1",
        payload={},
        signature_valid=True,
    )
    await process_incoming_event({}, event.model_dump(mode="json"), log_row["id"])

    user = await users.get_by_external("sendpulse", "instagram", "commenter_1")
    assert user is not None
    conv = await conversations.get_active(user["id"], "instagram")
    msg = await db.fetchrow(
        "SELECT * FROM messages WHERE conversation_id = $1", conv["id"],
    )
    assert msg["source"] == "comment"
    assert msg["text"] == "ОЧИЩЕНИЕ"
