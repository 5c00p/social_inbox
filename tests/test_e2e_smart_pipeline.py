"""E2E: returning user without keyword match → smart scenario fallback → Claude reply."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import AsyncClient

from app.models.events import IncomingEvent
from app.repos import events as events_repo
from app.repos import token_budget, users
from app.repos.redis_client import get_redis
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


async def test_returning_user_smart_reply(
    client: AsyncClient,
    fake_provider: FakeProvider,
    db: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Pre-create user and mark welcome as sent
    user = await users.create(
        provider_name="sendpulse",
        platform="instagram",
        external_id="e2e_smart_user",
        full_name="Anna",
    )
    await lead_tracker.mark_welcome_sent(user["id"])

    # Mock Anthropic
    fake_response = _FakeResponse(
        content=[_FakeContentText(text="В программе 30 дней с эфирными маслами 🌿")],
        usage=_FakeUsage(input_tokens=200, output_tokens=50),
    )
    fake_client = MagicMock()
    fake_messages = MagicMock()
    fake_messages.create = AsyncMock(return_value=fake_response)
    fake_client.messages = fake_messages
    monkeypatch.setattr(claude_responder, "_client", fake_client)

    # Reset budget
    redis = await get_redis()
    await redis.delete(token_budget._input_key(user["id"]))
    await redis.delete(token_budget._output_key(user["id"]))

    # Send DM with no matching keyword (so engine falls back to smart)
    event = IncomingEvent(
        provider="sendpulse",
        platform="instagram",
        event_type="message",
        external_user_id="e2e_smart_user",
        external_event_id="e2e_smart_evt",
        username="anna_p",
        text="А что входит в программу?",
        occurred_at=datetime(2026, 5, 8, 12, 0, tzinfo=UTC),
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

    # Anthropic was called once
    fake_messages.create.assert_called_once()

    # Outgoing message recorded with claude metadata
    msg = await db.fetchrow(
        """
        SELECT m.* FROM messages m
        JOIN conversations c ON c.id = m.conversation_id
        WHERE c.user_id = $1 AND m.direction = 'out'
        ORDER BY m.created_at DESC
        LIMIT 1
        """,
        user["id"],
    )
    assert msg is not None
    assert "30 дней" in msg["text"]
    assert msg["claude_used"] is True
    assert msg["claude_model"] == "claude-sonnet-4-6"
    assert msg["claude_tokens_in"] == 200
    assert msg["claude_tokens_out"] == 50

    # FakeProvider received the message
    assert len(fake_provider.sent) == 1
    assert "30 дней" in fake_provider.sent[0].text
