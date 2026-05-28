"""Read access to conversations + writes for admin actions."""

from __future__ import annotations

from typing import Any

import asyncpg

from admin.data import _db


async def list_conversations(
    *,
    status_filter: str | None = None,
    search_username: str | None = None,
    limit: int = 100,
) -> list[asyncpg.Record]:
    """Return conversations enriched with user info, sorted handover-pending first."""
    where = ["u.deleted_at IS NULL"]
    params: list[Any] = []
    if status_filter:
        params.append(status_filter)
        where.append(f"c.status = ${len(params)}")
    if search_username:
        params.append(f"%{search_username}%")
        where.append(f"u.username ILIKE ${len(params)}")

    params.append(limit)
    sql = f"""
        SELECT c.id AS conversation_id,
               c.status, c.last_message_at, c.created_at, c.handover_reason,
               u.id AS user_id, u.platform, u.username, u.full_name,
               u.short_id, u.tg_handover_at, u.smart_mode_enabled
        FROM conversations c
        JOIN social_users u ON u.id = c.user_id
        WHERE {" AND ".join(where)}
        ORDER BY
            CASE c.status WHEN 'handover_pending' THEN 0 ELSE 1 END,
            c.last_message_at DESC
        LIMIT ${len(params)}
    """
    return await _db.fetch(sql, *params)


async def get_conversation(conversation_id: int) -> asyncpg.Record | None:
    return await _db.fetchrow(
        """
        SELECT c.*, u.platform AS user_platform, u.username, u.full_name,
               u.short_id, u.smart_mode_enabled, u.external_id, u.tg_user_id
        FROM conversations c
        JOIN social_users u ON u.id = c.user_id
        WHERE c.id = $1
        """,
        conversation_id,
    )


async def close_handover(conversation_id: int) -> None:
    await _db.execute(
        """
        UPDATE conversations
        SET status = 'handover_done', closed_at = NOW()
        WHERE id = $1
        """,
        conversation_id,
    )


async def reopen_conversation(conversation_id: int) -> None:
    await _db.execute(
        """
        UPDATE conversations
        SET status = 'active', closed_at = NULL
        WHERE id = $1
        """,
        conversation_id,
    )


async def set_smart_mode(user_id: int, enabled: bool) -> None:
    await _db.execute(
        "UPDATE social_users SET smart_mode_enabled = $2 WHERE id = $1",
        user_id,
        enabled,
    )
