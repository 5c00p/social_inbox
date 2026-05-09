"""Keywords CRUD for admin."""
from __future__ import annotations

import asyncpg

from app.repos.pool import get_pool


async def list_all() -> list[asyncpg.Record]:
    pool = await get_pool()
    return await pool.fetch(
        """
        SELECT k.id, k.keyword, k.match_type, k.context, k.scenario_id,
               k.priority, k.case_sensitive, k.active,
               s.name AS scenario_name
        FROM keywords k
        LEFT JOIN scenarios s ON s.id = k.scenario_id
        ORDER BY k.priority ASC, k.id ASC
        """
    )


async def create(
    *,
    keyword: str,
    match_type: str,
    context: str,
    scenario_id: int,
    priority: int = 100,
    case_sensitive: bool = False,
) -> int:
    pool = await get_pool()
    row = await pool.fetchrow(
        """
        INSERT INTO keywords (keyword, match_type, context, scenario_id,
                              priority, case_sensitive, active)
        VALUES ($1, $2, $3, $4, $5, $6, TRUE)
        RETURNING id
        """,
        keyword, match_type, context, scenario_id, priority, case_sensitive,
    )
    assert row is not None
    return row["id"]  # type: ignore[no-any-return]


async def update(
    keyword_id: int,
    *,
    keyword: str,
    match_type: str,
    context: str,
    scenario_id: int,
    priority: int,
    case_sensitive: bool,
    active: bool,
) -> None:
    pool = await get_pool()
    await pool.execute(
        """
        UPDATE keywords
        SET keyword=$2, match_type=$3, context=$4, scenario_id=$5,
            priority=$6, case_sensitive=$7, active=$8
        WHERE id = $1
        """,
        keyword_id, keyword, match_type, context, scenario_id,
        priority, case_sensitive, active,
    )


async def delete(keyword_id: int) -> None:
    pool = await get_pool()
    await pool.execute("DELETE FROM keywords WHERE id = $1", keyword_id)
