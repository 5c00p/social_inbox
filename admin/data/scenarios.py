"""Scenarios CRUD for admin."""

from __future__ import annotations

import asyncpg

from admin.data import _db


async def list_all() -> list[asyncpg.Record]:
    return await _db.fetch(
        """
        SELECT id, name, type, template, metadata, active, updated_at
        FROM scenarios
        ORDER BY type, id
        """
    )


async def get(scenario_id: int) -> asyncpg.Record | None:
    return await _db.fetchrow(
        "SELECT * FROM scenarios WHERE id = $1",
        scenario_id,
    )


async def update_template(scenario_id: int, template: str) -> None:
    await _db.execute(
        """
        UPDATE scenarios
        SET template = $2, updated_at = NOW()
        WHERE id = $1
        """,
        scenario_id,
        template,
    )


async def set_active(scenario_id: int, active: bool) -> None:
    await _db.execute(
        """
        UPDATE scenarios SET active = $2, updated_at = NOW() WHERE id = $1
        """,
        scenario_id,
        active,
    )
