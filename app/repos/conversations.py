"""Repository for conversations table."""
from __future__ import annotations

from datetime import datetime

import asyncpg

from app.models.enums import ConversationStatus, Platform
from app.repos.pool import get_pool


async def get_active(user_id: int, platform: Platform) -> asyncpg.Record | None:
    """Return active conversation for (user, platform) or None."""
    pool = await get_pool()
    return await pool.fetchrow(
        """
        SELECT * FROM conversations
        WHERE user_id = $1 AND platform = $2 AND status = 'active'
        ORDER BY created_at DESC
        LIMIT 1
        """,
        user_id, platform,
    )


async def create(user_id: int, platform: Platform) -> asyncpg.Record:
    """Create a new active conversation."""
    pool = await get_pool()
    return await pool.fetchrow(
        """
        INSERT INTO conversations (user_id, platform, status)
        VALUES ($1, $2, 'active')
        RETURNING *
        """,
        user_id, platform,
    )


async def get_or_create(user_id: int, platform: Platform) -> asyncpg.Record:
    """Return active conversation or create new one. Race-safe."""
    existing = await get_active(user_id, platform)
    if existing:
        return existing
    try:
        return await create(user_id, platform)
    except asyncpg.UniqueViolationError:
        # Concurrent creation — fetch the one that won.
        result = await get_active(user_id, platform)
        if result is None:
            raise
        return result


async def update_last_message_at(conversation_id: int, ts: datetime) -> None:
    """Bump last_message_at. Called on every message in this conversation."""
    pool = await get_pool()
    await pool.execute(
        "UPDATE conversations SET last_message_at = $2 WHERE id = $1",
        conversation_id, ts,
    )


async def set_status(
    conversation_id: int,
    status: ConversationStatus,
    reason: str | None = None,
) -> None:
    """Change status. If 'closed' — also set closed_at."""
    pool = await get_pool()
    if status == "closed":
        await pool.execute(
            """
            UPDATE conversations
            SET status = $2, closed_at = NOW(), handover_reason = $3
            WHERE id = $1
            """,
            conversation_id, status, reason,
        )
    else:
        await pool.execute(
            """
            UPDATE conversations
            SET status = $2, handover_reason = COALESCE($3, handover_reason)
            WHERE id = $1
            """,
            conversation_id, status, reason,
        )
