"""End-to-end test: comment event → worker → DM with deep-link."""
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
async def test_comment_triggers_dm_with_deep_link(
    client: AsyncClient,
    fake_provider: FakeProvider,
    db,
) -> None:
    """A comment containing 'ОЧИЩЕНИЕ' should produce a DM with a Telegram deep-link."""
    event = IncomingEvent(
        provider="sendpulse",
        platform="instagram",
        event_type="comment",
        external_user_id="e2e_cmt_user_1",
        external_event_id="e2e_cmt_evt_1",
        full_name="Лена Тестова",
        text="ОЧИЩЕНИЕ хочу знать больше",
        post_id="reel_abc123",
        comment_id="cmt_xyz789",
        occurred_at=datetime(2026, 4, 30, 12, 0, tzinfo=UTC),
    )
    fake_provider.queue_event(event)

    response = await client.post("/webhooks/sendpulse", json={})
    assert response.status_code == 200

    log_row = await db.fetchrow(
        "SELECT * FROM events_log WHERE external_event_id = $1",
        "e2e_cmt_evt_1",
    )
    assert log_row is not None

    await process_incoming_event({}, event.model_dump(mode="json"), log_row["id"])

    user = await users.get_by_external("sendpulse", "instagram", "e2e_cmt_user_1")
    assert user is not None

    assert len(fake_provider.sent) == 1
    msg = fake_provider.sent[0]
    assert msg.platform == "instagram"
    assert msg.external_user_id == "e2e_cmt_user_1"
    assert msg.reply_to_comment_id == "cmt_xyz789"
    assert msg.text is not None
    assert "t.me/yuliya_purify_bot" in msg.text
    assert f"ig_{user['short_id']}_purify" in msg.text

    assert await lead_tracker.was_welcome_sent(user["id"]) is True
