"""Tests for ScenarioEngine routing."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.models.events import IncomingEvent
from app.repos import conversations, users
from app.services import scenario_engine
from app.services.keyword_matcher import reset_cache


@pytest.fixture(autouse=True)
def _reset_state() -> None:
    reset_cache()
    yield  # type: ignore[misc]
    reset_cache()
    # Note: do NOT reset_registry() — handlers are registered at import time
    # and tests share the same process. Resetting would break subsequent tests.


def _make_event(
    text: str = "hi",
    event_type: str = "message",
    external_user_id: str = "engine_user",
    external_event_id: str = "evt_eng_1",
) -> IncomingEvent:
    return IncomingEvent(
        provider="sendpulse",
        platform="instagram",
        event_type=event_type,  # type: ignore[arg-type]
        external_user_id=external_user_id,
        external_event_id=external_event_id,
        text=text,
        occurred_at=datetime(2026, 4, 30, 12, 0, tzinfo=UTC),
    )


@pytest.mark.asyncio
async def test_engine_falls_back_to_echo_when_no_match(db) -> None:
    """No keywords seeded, no welcome → echo fallback fires."""
    user = await users.create(
        provider_name="sendpulse", platform="instagram", external_id="engine_no_match",
    )
    conv = await conversations.create(user["id"], "instagram")

    event = _make_event(text="random text", external_user_id="engine_no_match")
    msg = await scenario_engine.handle(event, user, conv, is_new_user=False)

    assert msg is not None
    assert msg.text is not None
    assert "Получено:" in msg.text
    assert "random text" in msg.text


@pytest.mark.asyncio
async def test_engine_returns_none_for_handover_conversation(db) -> None:
    user = await users.create(
        provider_name="sendpulse", platform="instagram", external_id="engine_handover",
    )
    conv = await conversations.create(user["id"], "instagram")
    await conversations.set_status(conv["id"], "handover_pending", reason="test")

    # Use direct fetch since get_active filters to status='active'.
    conv_handover = await db.fetchrow("SELECT * FROM conversations WHERE id = $1", conv["id"])

    event = _make_event(external_user_id="engine_handover")
    msg = await scenario_engine.handle(event, user, conv_handover, is_new_user=False)

    assert msg is None  # handover state suppresses replies


@pytest.mark.asyncio
async def test_engine_uses_keyword_scenario(db) -> None:
    """When a keyword matches, the keyword's scenario_id is used."""
    sid_row = await db.fetchrow(
        """
        INSERT INTO scenarios (name, type, template, active)
        VALUES ('keyword_test_scenario', 'echo', 'kw template', TRUE)
        RETURNING id
        """,
    )
    sid = sid_row["id"]

    await db.execute(
        """
        INSERT INTO keywords (keyword, match_type, context, scenario_id, priority)
        VALUES ($1, 'exact', 'dm', $2, 10)
        """,
        "ОЧИЩЕНИЕ", sid,
    )

    user = await users.create(
        provider_name="sendpulse", platform="instagram", external_id="engine_kw",
    )
    conv = await conversations.create(user["id"], "instagram")

    event = _make_event(text="ОЧИЩЕНИЕ", external_user_id="engine_kw")
    msg = await scenario_engine.handle(event, user, conv, is_new_user=False)

    assert msg is not None
    assert msg.scenario_id == sid


@pytest.mark.asyncio
async def test_register_scenario_duplicate_raises() -> None:
    """Registering same type twice should raise."""
    from app.services.scenario_engine import register_scenario

    @register_scenario("__test_dup")
    async def first(event, user, conv, scenario):
        return None

    with pytest.raises(RuntimeError, match="already registered"):
        @register_scenario("__test_dup")
        async def second(event, user, conv, scenario):
            return None

    # Cleanup so __test_dup doesn't leak between test runs
    scenario_engine._registry.pop("__test_dup", None)
