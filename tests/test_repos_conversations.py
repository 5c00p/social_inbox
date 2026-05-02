"""Tests for app.repos.conversations."""
from __future__ import annotations

import pytest

from app.repos import conversations, users


@pytest.mark.asyncio
async def test_get_or_create_returns_same_conversation(db) -> None:  # type: ignore[no-untyped-def]
    user = await users.create(
        provider_name="sendpulse", platform="instagram", external_id="conv_user_1",
    )
    c1 = await conversations.get_or_create(user["id"], "instagram")
    c2 = await conversations.get_or_create(user["id"], "instagram")
    assert c1["id"] == c2["id"]
    assert c1["status"] == "active"


@pytest.mark.asyncio
async def test_set_status_handover(db) -> None:  # type: ignore[no-untyped-def]
    user = await users.create(
        provider_name="sendpulse", platform="instagram", external_id="conv_user_2",
    )
    conv = await conversations.create(user["id"], "instagram")
    await conversations.set_status(conv["id"], "handover_pending", reason="medical_question")

    pool_conn = db
    row = await pool_conn.fetchrow("SELECT * FROM conversations WHERE id = $1", conv["id"])
    assert row["status"] == "handover_pending"
    assert row["handover_reason"] == "medical_question"
