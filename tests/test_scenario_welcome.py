"""Tests for welcome scenario handler."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.models.events import IncomingEvent
from app.repos import conversations, users
from app.repos import scenarios as scenarios_repo
from app.repos.redis_client import get_redis
from app.services import lead_tracker
from app.services.scenarios.welcome import handle_welcome


def _make_event(
    text: str = "Привет",
    external_user_id: str = "welcome_user",
) -> IncomingEvent:
    return IncomingEvent(
        provider="sendpulse",
        platform="instagram",
        event_type="message",
        external_user_id=external_user_id,
        external_event_id=f"evt_w_{external_user_id}",
        full_name="Маша Петрова",
        text=text,
        occurred_at=datetime(2026, 4, 30, 12, 0, tzinfo=UTC),
    )


async def _setup(db, external_id: str = "welcome_user"):  # type: ignore[no-untyped-def]
    """Create user, conversation, fetch welcome scenario row, clear redis flag."""
    user = await users.create(
        provider_name="sendpulse",
        platform="instagram",
        external_id=external_id,
        full_name="Маша Петрова",
    )
    conv = await conversations.create(user["id"], "instagram")
    scenario = await scenarios_repo.get_by_name("default_welcome")
    assert scenario is not None, "default_welcome should be seeded by migration 007"

    redis = await get_redis()
    await redis.delete(lead_tracker._welcome_key(user["id"]))

    return user, conv, scenario


@pytest.mark.asyncio
async def test_welcome_returns_message_with_deep_link(db) -> None:
    user, conv, scenario = await _setup(db, "welcome_basic")
    event = _make_event(external_user_id="welcome_basic")

    msg = await handle_welcome(event, user, conv, scenario)

    assert msg is not None
    assert msg.text is not None
    assert "Маша" in msg.text
    assert f"ig_{user['short_id']}_purify" in msg.text
    assert "автоматический помощник" in msg.text
    assert msg.scenario_id == scenario["id"]


@pytest.mark.asyncio
async def test_welcome_quick_replies_resolved(db) -> None:
    user, conv, scenario = await _setup(db, "welcome_qr")
    event = _make_event(external_user_id="welcome_qr")

    msg = await handle_welcome(event, user, conv, scenario)

    assert msg is not None
    assert msg.quick_replies is not None
    assert len(msg.quick_replies) == 2

    tg_button = msg.quick_replies[0]
    assert tg_button.title == "Перейти в Telegram"
    assert "https://t.me/yuliya_purify_bot" in tg_button.payload
    assert user["short_id"] in tg_button.payload

    info_button = msg.quick_replies[1]
    assert info_button.title == "Узнать больше"
    assert info_button.payload == "more_info"


@pytest.mark.asyncio
async def test_welcome_lifetime_idempotency(db) -> None:
    user, conv, scenario = await _setup(db, "welcome_idem")
    event = _make_event(external_user_id="welcome_idem")

    msg1 = await handle_welcome(event, user, conv, scenario)
    assert msg1 is not None

    msg2 = await handle_welcome(event, user, conv, scenario)
    assert msg2 is None


@pytest.mark.asyncio
async def test_welcome_uses_default_name_for_anonymous_user(db) -> None:
    user = await users.create(
        provider_name="sendpulse",
        platform="instagram",
        external_id="welcome_anon",
    )
    conv = await conversations.create(user["id"], "instagram")
    scenario = await scenarios_repo.get_by_name("default_welcome")
    assert scenario is not None
    redis = await get_redis()
    await redis.delete(lead_tracker._welcome_key(user["id"]))

    event = _make_event(external_user_id="welcome_anon")
    event_anon = event.model_copy(update={"full_name": None})

    msg = await handle_welcome(event_anon, user, conv, scenario)

    assert msg is not None
    assert msg.text is not None
    assert "дорогая" in msg.text


@pytest.mark.asyncio
async def test_welcome_respects_metadata_slug(db) -> None:
    """A custom welcome scenario with metadata.tg_scenario_slug='oils'."""
    custom = await db.fetchrow(
        """
        INSERT INTO scenarios (name, type, template, metadata, active)
        VALUES (
            'oils_welcome',
            'welcome',
            'Привет, {first_name}! Перейди: {tg_link}\n{disclaimer}',
            '{"tg_scenario_slug": "oils"}'::jsonb,
            TRUE
        )
        RETURNING *
        """
    )
    user = await users.create(
        provider_name="sendpulse",
        platform="instagram",
        external_id="welcome_oils",
        full_name="Анна",
    )
    conv = await conversations.create(user["id"], "instagram")
    redis = await get_redis()
    await redis.delete(lead_tracker._welcome_key(user["id"]))

    event = _make_event(external_user_id="welcome_oils")
    msg = await handle_welcome(event, user, conv, custom)

    assert msg is not None
    assert msg.text is not None
    assert f"ig_{user['short_id']}_oils" in msg.text
