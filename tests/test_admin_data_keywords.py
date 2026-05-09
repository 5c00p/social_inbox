"""Tests for admin data layer — keywords CRUD."""
from __future__ import annotations

from typing import Any

from admin.data import keywords as kw_data


async def _seed_scenario(db: Any, name: str = "kw_test") -> int:
    row = await db.fetchrow(
        "INSERT INTO scenarios (name, type, active) VALUES ($1, 'echo', TRUE) RETURNING id",
        name,
    )
    return row["id"]  # type: ignore[no-any-return]


async def test_create_and_list(db: Any) -> None:
    sid = await _seed_scenario(db, "kw_create")
    new_id = await kw_data.create(
        keyword="ОЧИЩЕНИЕ", match_type="contains", context="comment",
        scenario_id=sid, priority=50,
    )
    rows = await kw_data.list_all()
    target = next(r for r in rows if r["id"] == new_id)
    assert target["keyword"] == "ОЧИЩЕНИЕ"
    assert target["scenario_name"] == "kw_create"


async def test_update(db: Any) -> None:
    sid = await _seed_scenario(db, "kw_update")
    new_id = await kw_data.create(
        keyword="X", match_type="exact", context="dm",
        scenario_id=sid, priority=100,
    )
    await kw_data.update(
        new_id,
        keyword="Y", match_type="contains", context="both",
        scenario_id=sid, priority=10, case_sensitive=True, active=False,
    )
    row = await db.fetchrow("SELECT * FROM keywords WHERE id = $1", new_id)
    assert row["keyword"] == "Y"
    assert row["context"] == "both"
    assert row["active"] is False


async def test_delete(db: Any) -> None:
    sid = await _seed_scenario(db, "kw_delete")
    new_id = await kw_data.create(
        keyword="DEL", match_type="exact", context="dm",
        scenario_id=sid,
    )
    await kw_data.delete(new_id)
    row = await db.fetchrow("SELECT * FROM keywords WHERE id = $1", new_id)
    assert row is None
