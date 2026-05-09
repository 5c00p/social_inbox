"""End-to-end test: new user's first DM triggers welcome with deep-link."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import AsyncClient

from app.models.events import IncomingEvent
from app.repos import events as events_repo
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
async def test_new_user_first_dm_triggers_welcome(
    client: AsyncClient,
    fake_provider: FakeProvider,
    db,
) -> None:
    event = IncomingEvent(
        provider="sendpulse",
        platform="instagram",
        event_type="message",
        external_user_id="e2e_welcome_user",
        external_event_id="e2e_welcome_evt_1",
        username="masha_p",
        full_name="Маша Петрова",
        text="Привет",
        occurred_at=datetime(2026, 4, 30, 12, 0, tzinfo=UTC),
    )
    fake_provider.queue_event(event)

    response = await client.post("/webhooks/sendpulse", json={})
    assert response.status_code == 200

    log_row = await db.fetchrow(
        "SELECT * FROM events_log WHERE external_event_id = $1",
        "e2e_welcome_evt_1",
    )
    assert log_row is not None

    await process_incoming_event({}, event.model_dump(mode="json"), log_row["id"])

    user = await users.get_by_external("sendpulse", "instagram", "e2e_welcome_user")
    assert user is not None
    assert user["short_id"] is not None

    msgs = await db.fetch(
        """
        SELECT m.* FROM messages m
        JOIN conversations c ON c.id = m.conversation_id
        WHERE c.user_id = $1 AND m.direction = 'out'
        """,
        user["id"],
    )
    assert len(msgs) == 1
    out_msg = msgs[0]
    assert "Маша" in out_msg["text"]
    assert f"ig_{user['short_id']}_purify" in out_msg["text"]

    assert len(fake_provider.sent) == 1
    sent = fake_provider.sent[0]
    assert sent.quick_replies is not None
    assert len(sent.quick_replies) == 2

    assert await lead_tracker.was_welcome_sent(user["id"]) is True


@pytest.mark.asyncio
async def test_returning_user_does_not_get_welcome_again(
    client: AsyncClient,
    fake_provider: FakeProvider,
    db,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If welcome was already sent (Redis flag set), second message → smart fallback, no deep-link."""
    # Mock Anthropic so smart scenario produces a reply
    fake_client = MagicMock()
    fake_messages = MagicMock()
    fake_messages.create = AsyncMock(return_value=_FakeResponse(
        content=[_FakeContentText(text="Конечно, расскажу подробнее 🌿")],
        usage=_FakeUsage(input_tokens=60, output_tokens=12),
    ))
    fake_client.messages = fake_messages
    monkeypatch.setattr(claude_responder, "_client", fake_client)

    user = await users.create(
        provider_name="sendpulse",
        platform="instagram",
        external_id="e2e_returning",
        full_name="Anna",
    )
    await lead_tracker.mark_welcome_sent(user["id"])

    event = IncomingEvent(
        provider="sendpulse",
        platform="instagram",
        event_type="message",
        external_user_id="e2e_returning",
        external_event_id="e2e_ret_evt_1",
        username="anna",
        text="Hi again",
        occurred_at=datetime.now(UTC),
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

    msgs = await db.fetch(
        """
        SELECT m.* FROM messages m
        JOIN conversations c ON c.id = m.conversation_id
        WHERE c.user_id = $1 AND m.direction = 'out'
        ORDER BY m.created_at ASC
        """,
        user["id"],
    )
    assert len(msgs) == 1
    out_text = msgs[0]["text"]
    # Smart reply should not contain a Telegram deep-link (that's only in welcome)
    assert "ig_" not in out_text
    assert msgs[0]["claude_used"] is True
