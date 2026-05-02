"""Tests for comment_to_dm scenario handler."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.models.events import IncomingEvent
from app.repos import conversations, users
from app.repos import scenarios as scenarios_repo
from app.repos.redis_client import get_redis
from app.services import lead_tracker
from app.services.scenarios.comment_to_dm import handle_comment_to_dm


def _make_comment_event(
    text: str = "ОЧИЩЕНИЕ хочу",
    external_user_id: str = "comment_user",
    post_id: str = "post_123",
    comment_id: str = "cmt_1",
) -> IncomingEvent:
    return IncomingEvent(
        provider="sendpulse",
        platform="instagram",
        event_type="comment",
        external_user_id=external_user_id,
        external_event_id=f"evt_c_{comment_id}",
        full_name="Маша Петрова",
        text=text,
        post_id=post_id,
        comment_id=comment_id,
        occurred_at=datetime(2026, 4, 30, 12, 0, tzinfo=UTC),
    )


async def _setup(db, external_id: str = "comment_user"):  # type: ignore[no-untyped-def]
    user = await users.create(
        provider_name="sendpulse",
        platform="instagram",
        external_id=external_id,
        full_name="Маша Петрова",
    )
    conv = await conversations.create(user["id"], "instagram")
    scenario = await scenarios_repo.get_by_name("default_purify_comment")
    assert scenario is not None, "default_purify_comment should be seeded"
    redis = await get_redis()
    await redis.delete(lead_tracker._welcome_key(user["id"]))
    return user, conv, scenario


@pytest.mark.asyncio
async def test_comment_to_dm_returns_message(db) -> None:
    user, conv, scenario = await _setup(db, "cmt_basic")
    event = _make_comment_event(external_user_id="cmt_basic")

    msg = await handle_comment_to_dm(event, user, conv, scenario)

    assert msg is not None
    assert msg.text is not None
    assert "Маша" in msg.text
    assert f"ig_{user['short_id']}_purify" in msg.text
    assert msg.reply_to_comment_id == "cmt_1"
    assert msg.scenario_id == scenario["id"]


@pytest.mark.asyncio
async def test_comment_to_dm_quick_reply_resolved(db) -> None:
    user, conv, scenario = await _setup(db, "cmt_qr")
    event = _make_comment_event(external_user_id="cmt_qr")

    msg = await handle_comment_to_dm(event, user, conv, scenario)

    assert msg is not None
    assert msg.quick_replies is not None
    assert len(msg.quick_replies) == 1
    assert "https://t.me/yuliya_purify_bot" in msg.quick_replies[0].payload
    assert user["short_id"] in msg.quick_replies[0].payload


@pytest.mark.asyncio
async def test_comment_to_dm_per_post_idempotency(db) -> None:
    user, conv, scenario = await _setup(db, "cmt_idem")

    event1 = _make_comment_event(external_user_id="cmt_idem", comment_id="cmt_A")
    msg1 = await handle_comment_to_dm(event1, user, conv, scenario)
    assert msg1 is not None

    # Second comment from same user on same post → should be skipped
    event2 = _make_comment_event(external_user_id="cmt_idem", comment_id="cmt_B")
    msg2 = await handle_comment_to_dm(event2, user, conv, scenario)
    assert msg2 is None


@pytest.mark.asyncio
async def test_comment_to_dm_skipped_if_welcome_sent(db) -> None:
    user, conv, scenario = await _setup(db, "cmt_skip_welcome")
    await lead_tracker.mark_welcome_sent(user["id"])

    event = _make_comment_event(external_user_id="cmt_skip_welcome")
    msg = await handle_comment_to_dm(event, user, conv, scenario)
    assert msg is None


@pytest.mark.asyncio
async def test_comment_to_dm_skipped_for_non_comment_event(db) -> None:
    user, conv, scenario = await _setup(db, "cmt_type_guard")
    event = IncomingEvent(
        provider="sendpulse",
        platform="instagram",
        event_type="message",
        external_user_id="cmt_type_guard",
        external_event_id="evt_msg_1",
        text="ОЧИЩЕНИЕ",
        occurred_at=datetime(2026, 4, 30, 12, 0, tzinfo=UTC),
    )

    msg = await handle_comment_to_dm(event, user, conv, scenario)
    assert msg is None


@pytest.mark.asyncio
async def test_comment_to_dm_marks_welcome_sent(db) -> None:
    user, conv, scenario = await _setup(db, "cmt_marks_welcome")
    event = _make_comment_event(external_user_id="cmt_marks_welcome")

    assert not await lead_tracker.was_welcome_sent(user["id"])
    msg = await handle_comment_to_dm(event, user, conv, scenario)
    assert msg is not None
    assert await lead_tracker.was_welcome_sent(user["id"])
