"""Tests for lead_tracker service."""
from __future__ import annotations

import pytest

from app.repos.redis_client import get_redis
from app.services import lead_tracker


def test_build_deep_link_default_slug() -> None:
    url = lead_tracker.build_deep_link("Kd7nQ2x9")
    assert url == "https://t.me/yuliya_purify_bot?start=ig_Kd7nQ2x9_purify"


def test_build_deep_link_custom_slug() -> None:
    url = lead_tracker.build_deep_link("abc123", "oils")
    assert url == "https://t.me/yuliya_purify_bot?start=ig_abc123_oils"


def test_build_deep_link_rejects_underscore_in_slug() -> None:
    with pytest.raises(ValueError, match="Invalid scenario_slug"):
        lead_tracker.build_deep_link("abc123", "purify_v2")


def test_build_deep_link_rejects_uppercase_in_slug() -> None:
    with pytest.raises(ValueError):
        lead_tracker.build_deep_link("abc123", "Purify")


def test_build_deep_link_accepts_hyphen_in_slug() -> None:
    url = lead_tracker.build_deep_link("abc123", "early-bird")
    assert "ig_abc123_early-bird" in url


@pytest.mark.asyncio
async def test_welcome_flag_lifecycle() -> None:
    redis = await get_redis()
    user_id = 99001
    await redis.delete(lead_tracker._welcome_key(user_id))

    assert await lead_tracker.was_welcome_sent(user_id) is False
    await lead_tracker.mark_welcome_sent(user_id)
    assert await lead_tracker.was_welcome_sent(user_id) is True


@pytest.mark.asyncio
async def test_record_handover_updates_user(db) -> None:
    from app.repos import users

    user = await users.create(
        provider_name="sendpulse",
        platform="instagram",
        external_id="handover_user_1",
    )
    await lead_tracker.record_handover(user["id"], tg_user_id=12345)

    refreshed = await db.fetchrow(
        "SELECT tg_handover_at, tg_user_id FROM social_users WHERE id = $1",
        user["id"],
    )
    assert refreshed["tg_handover_at"] is not None
    assert refreshed["tg_user_id"] == 12345
