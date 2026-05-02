"""Tests that ScenarioEngine routes comment events correctly."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.models.events import IncomingEvent
from app.repos import conversations, users
from app.repos.pool import get_pool
from app.services.scenario_engine import handle


def _comment(
    text: str,
    external_user_id: str,
    post_id: str = "post_1",
    comment_id: str = "cmt_1",
) -> IncomingEvent:
    return IncomingEvent(
        provider="sendpulse",
        platform="instagram",
        event_type="comment",
        external_user_id=external_user_id,
        external_event_id=f"evt_{comment_id}",
        text=text,
        post_id=post_id,
        comment_id=comment_id,
        occurred_at=datetime(2026, 4, 30, 12, 0, tzinfo=UTC),
    )


@pytest.mark.usefixtures("_db_setup")
async def test_global_keyword_triggers_comment_to_dm() -> None:
    user = await users.create(
        provider_name="sendpulse",
        platform="instagram",
        external_id="eng_cmt_1",
        full_name="Тест Юзер",
    )
    conv = await conversations.create(user["id"], "instagram")

    event = _comment("хочу очищение пожалуйста", "eng_cmt_1")
    msg = await handle(event, user, conv, is_new_user=False)

    assert msg is not None
    assert msg.reply_to_comment_id == "cmt_1"
    assert f"ig_{user['short_id']}_purify" in (msg.text or "")


@pytest.mark.usefixtures("_db_setup")
async def test_post_specific_trigger_overrides_global_keyword() -> None:
    pool = await get_pool()

    # Insert a post-specific trigger pointing at a scenario with a different slug
    custom_scenario_id = await pool.fetchval(
        """
        INSERT INTO scenarios (name, type, template, metadata, active)
        VALUES (
            'oils_comment_test',
            'comment_to_dm',
            'Привет, {first_name}! {tg_link}\n{disclaimer}',
            '{"tg_scenario_slug": "oils"}'::jsonb,
            TRUE
        )
        RETURNING id
        """
    )
    await pool.execute(
        """
        INSERT INTO comment_triggers (platform, post_id, keyword, scenario_id, active)
        VALUES ('instagram', 'special_post', 'масла', $1, TRUE)
        """,
        custom_scenario_id,
    )

    user = await users.create(
        provider_name="sendpulse",
        platform="instagram",
        external_id="eng_cmt_2",
        full_name="Тест Юзер 2",
    )
    conv = await conversations.create(user["id"], "instagram")

    event = _comment("хочу масла", "eng_cmt_2", post_id="special_post", comment_id="cmt_2")
    msg = await handle(event, user, conv, is_new_user=False)

    assert msg is not None
    assert f"ig_{user['short_id']}_oils" in (msg.text or "")
