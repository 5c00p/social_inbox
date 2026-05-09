"""Scenarios CRUD for admin."""
from __future__ import annotations

import asyncpg

from app.repos.pool import get_pool


async def list_all() -> list[asyncpg.Record]:
    pool = await get_pool()
    return await pool.fetch(
        """
        SELECT id, name, type, template, metadata, active, updated_at
        FROM scenarios
        ORDER BY type, id
        """
    )


async def get(scenario_id: int) -> asyncpg.Record | None:
    pool = await get_pool()
    return await pool.fetchrow(
        "SELECT * FROM scenarios WHERE id = $1", scenario_id,
    )


async def update_template(scenario_id: int, template: str) -> None:
    pool = await get_pool()
    await pool.execute(
        """
        UPDATE scenarios
        SET template = $2, updated_at = NOW()
        WHERE id = $1
        """,
        scenario_id, template,
    )


async def set_active(scenario_id: int, active: bool) -> None:
    pool = await get_pool()
    await pool.execute(
        """
        UPDATE scenarios SET active = $2, updated_at = NOW() WHERE id = $1
        """,
        scenario_id, active,
    )
