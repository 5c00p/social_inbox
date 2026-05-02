"""Tests for FakeProvider — the test double used by other tests.

We test the test double itself to make sure later tests can rely on it.
"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.models.events import IncomingEvent, OutgoingMessage
from tests.fakes.fake_provider import FakeProvider


def _make_event() -> IncomingEvent:
    return IncomingEvent(
        provider="sendpulse",
        platform="instagram",
        event_type="message",
        external_user_id="user_1",
        external_event_id="evt_1",
        text="Hello",
        occurred_at=datetime.now(UTC),
    )


@pytest.mark.asyncio
async def test_parse_webhook_returns_queued_events() -> None:
    fake = FakeProvider()
    event = _make_event()
    fake.queue_event(event)

    result = await fake.parse_webhook(b"body", {})
    assert result == [event]

    # Queue should be drained
    assert await fake.parse_webhook(b"body", {}) == []


@pytest.mark.asyncio
async def test_parse_webhook_empty_when_signature_invalid() -> None:
    fake = FakeProvider()
    fake.queue_event(_make_event())
    fake.signature_valid = False

    assert await fake.parse_webhook(b"body", {}) == []


@pytest.mark.asyncio
async def test_send_captures_messages() -> None:
    fake = FakeProvider()
    msg = OutgoingMessage(
        platform="instagram", external_user_id="u_1", text="Hi",
    )
    msg_id = await fake.send(msg)
    assert msg_id == "fake_msg_id_1"
    assert fake.sent == [msg]


@pytest.mark.asyncio
async def test_send_returns_none_on_failure() -> None:
    fake = FakeProvider()
    fake.send_should_fail = True

    msg = OutgoingMessage(
        platform="instagram", external_user_id="u_1", text="Hi",
    )
    result = await fake.send(msg)
    assert result is None
    assert fake.sent == []


@pytest.mark.asyncio
async def test_fetch_user_profile_returns_set_value() -> None:
    fake = FakeProvider()
    fake.set_profile("instagram", "u_1", {"username": "alice", "full_name": "Alice"})

    result = await fake.fetch_user_profile("instagram", "u_1")
    assert result == {"username": "alice", "full_name": "Alice"}


@pytest.mark.asyncio
async def test_fetch_user_profile_returns_empty_when_unset() -> None:
    fake = FakeProvider()
    assert await fake.fetch_user_profile("instagram", "unknown") == {}


def test_reset_clears_state() -> None:
    fake = FakeProvider()
    fake.queue_event(_make_event())
    fake.sent.append(OutgoingMessage(
        platform="instagram", external_user_id="u_1", text="x",
    ))
    fake.reset()
    assert fake.queued_events == []
    assert fake.sent == []
