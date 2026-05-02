"""Repository for keywords table — used by KeywordMatcher.

Keywords are loaded as a list and cached in memory by KeywordMatcher.
This repo only provides bulk read; mutations happen via admin API (Task 15).
"""
from __future__ import annotations

from typing import Literal

import asyncpg

from app.repos.pool import get_pool

KeywordContext = Literal["dm", "comment", "both"]


async def list_active(context: KeywordContext) -> list[asyncpg.Record]:
    """Return active keywords applicable to the given context, ordered by priority asc."""
    pool = await get_pool()
    return await pool.fetch(  # type: ignore[no-any-return]
        """
        SELECT id, keyword, match_type, context, scenario_id, priority, case_sensitive
        FROM keywords
        WHERE active = TRUE
          AND context IN ($1, 'both')
        ORDER BY priority ASC, id ASC
        """,
        context,
    )
