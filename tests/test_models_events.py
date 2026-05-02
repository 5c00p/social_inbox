"""Tests for IncomingEvent / OutgoingMessage Pydantic models."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.models.events import IncomingEvent, OutgoingMessage, QuickReply


def test_incoming_event_minimal_fields() -> None:
    event = IncomingEvent(
        provider="sendpulse",
        platform="instagram",
        event_type="message",
        external_user_id="user_123",
        external_event_id="evt_abc",
        occurred_at=datetime(2026, 4, 30, 12, 0, tzinfo=UTC),
    )
    assert event.text is None
    assert event.raw_payload == {}


def test_incoming_event_rejects_unknown_platform() -> None:
    with pytest.raises(ValidationError):
        IncomingEvent(
            provider="sendpulse",
            platform="tiktok",  # type: ignore[arg-type]
            event_type="message",
            external_user_id="u",
            external_event_id="e",
            occurred_at=datetime.now(UTC),
        )


def test_incoming_event_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        IncomingEvent(
            provider="sendpulse",
            platform="instagram",
            event_type="message",
            external_user_id="u",
            external_event_id="e",
            occurred_at=datetime.now(UTC),
            unexpected_field="x",  # type: ignore[call-arg]
        )


def test_incoming_event_rejects_empty_external_user_id() -> None:
    with pytest.raises(ValidationError):
        IncomingEvent(
            provider="sendpulse",
            platform="instagram",
            event_type="message",
            external_user_id="",
            external_event_id="e",
            occurred_at=datetime.now(UTC),
        )


def test_incoming_event_is_frozen() -> None:
    event = IncomingEvent(
        provider="sendpulse",
        platform="instagram",
        event_type="message",
        external_user_id="u",
        external_event_id="e",
        occurred_at=datetime.now(UTC),
    )
    with pytest.raises(ValidationError):
        event.text = "modified"  # type: ignore[misc]


def test_outgoing_message_with_quick_replies() -> None:
    msg = OutgoingMessage(
        platform="instagram",
        external_user_id="user_1",
        text="Привет!",
        quick_replies=[
            QuickReply(title="Очищение", payload="purify"),
            QuickReply(title="Масла", payload="oils"),
        ],
    )
    assert len(msg.quick_replies or []) == 2


def test_quick_reply_title_max_length() -> None:
    with pytest.raises(ValidationError):
        QuickReply(title="x" * 21, payload="p")


def test_outgoing_message_serializes_to_json() -> None:
    """Critical: messages are JSON-serialized into the arq queue."""
    msg = OutgoingMessage(
        platform="facebook",
        external_user_id="u_1",
        text="Hello",
    )
    json_str = msg.model_dump_json()
    assert "facebook" in json_str

    restored = OutgoingMessage.model_validate_json(json_str)
    assert restored == msg


def test_incoming_event_serializes_with_datetime() -> None:
    event = IncomingEvent(
        provider="sendpulse",
        platform="instagram",
        event_type="comment",
        external_user_id="u_1",
        external_event_id="e_1",
        post_id="post_42",
        comment_id="comment_99",
        text="ОЧИЩЕНИЕ",
        occurred_at=datetime(2026, 4, 30, 12, 0, tzinfo=UTC),
    )
    data = event.model_dump(mode="json")
    assert data["occurred_at"] == "2026-04-30T12:00:00Z"
    restored = IncomingEvent.model_validate(data)
    assert restored == event
