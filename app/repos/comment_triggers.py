"""Repository for comment_triggers + comment_user_dedup tables.

comment_triggers — post-specific overrides for keyword routing.
    Use case: under Reels A, "ОЧИЩЕНИЕ" → scenario 'purify',
              under Reels B, "ОЧИЩЕНИЕ" → scenario 'oils'.
    Falls back to global keywords table if no row matches.

comment_user_dedup — per-(user, post, scenario) idempotency.
    Ensures we send exactly ONE DM even if the user posts 5 comments.
"""
from __future__ import annotations

import contextlib

import asyncpg

from app.repos.pool import get_pool

# ---- comment_triggers ----

async def find_for_post(
    platform: str,
    post_id: str,
    text: str,
) -> asyncpg.Record | None:
    """Find a post-specific trigger matching the comment text.

    Match logic: case-insensitive 'contains'. Simple by design — comment_triggers
    is a small admin-managed table, not a flexible keyword DSL.
    """
    pool = await get_pool()
    return await pool.fetchrow(
        """
        SELECT * FROM comment_triggers
        WHERE platform = $1
          AND post_id = $2
          AND active = TRUE
          AND $3 ILIKE '%' || keyword || '%'
        LIMIT 1
        """,
        platform, post_id, text,
    )


# ---- comment_user_dedup ----

async def already_replied(
    *,
    user_id: int,
    platform: str,
    post_id: str,
    scenario_id: int,
) -> bool:
    """Return True if we already replied to this user under this post for this scenario."""
    pool = await get_pool()
    row = await pool.fetchrow(
        """
        SELECT 1 FROM comment_user_dedup
        WHERE user_id = $1
          AND platform = $2
          AND post_id = $3
          AND scenario_id = $4
        """,
        user_id, platform, post_id, scenario_id,
    )
    return row is not None


async def mark_replied(
    *,
    user_id: int,
    platform: str,
    post_id: str,
    scenario_id: int,
) -> None:
    """Record that we sent a DM in response to this user's comment under this post."""
    pool = await get_pool()
    with contextlib.suppress(asyncpg.UniqueViolationError):
        await pool.execute(
            """
            INSERT INTO comment_user_dedup (user_id, platform, post_id, scenario_id)
            VALUES ($1, $2, $3, $4)
            """,
            user_id, platform, post_id, scenario_id,
        )
