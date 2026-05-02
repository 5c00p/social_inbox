"""Repository for scenarios table.

Used by ScenarioEngine to look up scenario records by id or by name/type.
"""
from __future__ import annotations

import asyncpg

from app.repos.pool import get_pool


async def get_by_id(scenario_id: int) -> asyncpg.Record | None:
    pool = await get_pool()
    return await pool.fetchrow(
        "SELECT * FROM scenarios WHERE id = $1 AND active = TRUE",
        scenario_id,
    )


async def get_by_name(name: str) -> asyncpg.Record | None:
    pool = await get_pool()
    return await pool.fetchrow(
        "SELECT * FROM scenarios WHERE name = $1 AND active = TRUE",
        name,
    )


async def get_default_welcome() -> asyncpg.Record | None:
    """Return the first active scenario of type='welcome'.

    Used when an unknown user sends their first DM and there's no keyword match.
    Returns None if no welcome scenario is configured.
    """
    pool = await get_pool()
    return await pool.fetchrow(
        """
        SELECT * FROM scenarios
        WHERE type = 'welcome' AND active = TRUE
        ORDER BY id ASC
        LIMIT 1
        """,
    )
