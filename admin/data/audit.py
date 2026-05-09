"""Audit log for admin actions."""
from __future__ import annotations

from typing import Any

import asyncpg

from app.repos.pool import get_pool


async def record_action(
    *,
    actor: str,
    action: str,
    target_type: str | None = None,
    target_id: int | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    pool = await get_pool()
    await pool.execute(
        """
        INSERT INTO admin_audit_log (actor, action, target_type, target_id, details)
        VALUES ($1, $2, $3, $4, $5)
        """,
        actor, action, target_type, target_id, details or {},
    )


async def recent(limit: int = 50) -> list[asyncpg.Record]:
    pool = await get_pool()
    return await pool.fetch(
        """
        SELECT actor, action, target_type, target_id, details, created_at
        FROM admin_audit_log
        ORDER BY created_at DESC
        LIMIT $1
        """,
        limit,
    )
