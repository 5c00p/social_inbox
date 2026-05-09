"""Tests for admin data layer — conversations."""
from __future__ import annotations

from typing import Any

from admin.data import conversations as conv_data
from app.repos import conversations as conv_repo
from app.repos import users


async def test_list_conversations_handover_first(db: Any) -> None:
    u_active = await users.create(
        provider_name="sendpulse", platform="instagram",
        external_id="ad_active", username="active_u",
    )
    await conv_repo.create(u_active["id"], "instagram")

    u_handover = await users.create(
        provider_name="sendpulse", platform="instagram",
        external_id="ad_handover", username="handover_u",
    )
    c_handover = await conv_repo.create(u_handover["id"], "instagram")
    await conv_repo.set_status(c_handover["id"], "handover_pending", reason="test")

    rows = await conv_data.list_conversations(limit=100)
    statuses = [r["status"] for r in rows]
    pending_index = statuses.index("handover_pending")
    active_index = statuses.index("active")
    assert pending_index < active_index


async def test_list_conversations_filter_by_status(db: Any) -> None:
    u = await users.create(
        provider_name="sendpulse", platform="instagram",
        external_id="ad_filter",
    )
    await conv_repo.create(u["id"], "instagram")

    rows = await conv_data.list_conversations(status_filter="closed")
    assert all(r["status"] == "closed" for r in rows)


async def test_close_handover_changes_status(db: Any) -> None:
    u = await users.create(
        provider_name="sendpulse", platform="instagram",
        external_id="ad_close",
    )
    c = await conv_repo.create(u["id"], "instagram")
    await conv_repo.set_status(c["id"], "handover_pending", reason="x")

    await conv_data.close_handover(c["id"])

    row = await db.fetchrow("SELECT * FROM conversations WHERE id = $1", c["id"])
    assert row["status"] == "handover_done"
    assert row["closed_at"] is not None


async def test_set_smart_mode(db: Any) -> None:
    u = await users.create(
        provider_name="sendpulse", platform="instagram",
        external_id="ad_smart",
    )
    await conv_data.set_smart_mode(u["id"], False)
    refreshed = await db.fetchrow(
        "SELECT smart_mode_enabled FROM social_users WHERE id = $1", u["id"],
    )
    assert refreshed["smart_mode_enabled"] is False
