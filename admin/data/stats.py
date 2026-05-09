"""Aggregate statistics for the dashboard."""
from __future__ import annotations

import asyncpg

from app.repos.pool import get_pool


async def daily_new_leads(days: int = 14) -> list[asyncpg.Record]:
    """Number of new social_users per day, last N days."""
    pool = await get_pool()
    return await pool.fetch(
        """
        SELECT DATE_TRUNC('day', first_seen_at) AS day,
               COUNT(*)::int AS count
        FROM social_users
        WHERE first_seen_at >= NOW() - ($1 * INTERVAL '1 day')
          AND deleted_at IS NULL
        GROUP BY day
        ORDER BY day
        """,
        days,
    )


async def conversion_to_telegram(days: int = 30) -> dict[str, int]:
    """Of users seen in last N days, how many also have tg_user_id?"""
    pool = await get_pool()
    row = await pool.fetchrow(
        """
        SELECT
            COUNT(*) FILTER (WHERE first_seen_at >= NOW() - ($1 * INTERVAL '1 day'))::int AS total,
            COUNT(*) FILTER (
                WHERE first_seen_at >= NOW() - ($1 * INTERVAL '1 day')
                  AND tg_handover_at IS NOT NULL
            )::int AS converted
        FROM social_users
        WHERE deleted_at IS NULL
        """,
        days,
    )
    assert row is not None
    return {"total": row["total"], "converted": row["converted"]}


async def handover_breakdown(days: int = 30) -> list[asyncpg.Record]:
    """How many handovers per source (operator_request, symptom, claude_tool, etc.)."""
    pool = await get_pool()
    return await pool.fetch(
        """
        SELECT
            COALESCE(SPLIT_PART(handover_reason, ':', 1), 'unknown') AS source,
            COUNT(*)::int AS count
        FROM conversations
        WHERE status IN ('handover_pending', 'handover_done')
          AND created_at >= NOW() - ($1 * INTERVAL '1 day')
        GROUP BY source
        ORDER BY count DESC
        """,
        days,
    )


async def claude_token_usage(days: int = 7) -> list[asyncpg.Record]:
    """Daily Claude token cost (input + output)."""
    pool = await get_pool()
    return await pool.fetch(
        """
        SELECT DATE_TRUNC('day', created_at) AS day,
               SUM(claude_tokens_in)::int  AS tokens_in,
               SUM(claude_tokens_out)::int AS tokens_out,
               COUNT(*) FILTER (WHERE claude_used)::int AS messages_count
        FROM messages
        WHERE created_at >= NOW() - ($1 * INTERVAL '1 day')
          AND claude_used = TRUE
        GROUP BY day
        ORDER BY day
        """,
        days,
    )
