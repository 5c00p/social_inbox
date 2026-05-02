"""Repository for messages table."""
from __future__ import annotations

from typing import Any

import asyncpg

from app.models.enums import Direction
from app.repos.pool import get_pool


async def insert(
    *,
    conversation_id: int,
    direction: Direction,
    text: str | None,
    media_url: str | None = None,
    media_type: str | None = None,
    source: str | None = None,
    scenario_id: int | None = None,
    claude_used: bool = False,
    claude_model: str | None = None,
    claude_tokens_in: int | None = None,
    claude_tokens_out: int | None = None,
    safety_blocked: bool = False,
    safety_reason: str | None = None,
    external_message_id: str | None = None,
    raw_payload: dict[str, Any] | None = None,
) -> asyncpg.Record | None:
    """Insert a message. Idempotent on external_message_id.

    Returns the inserted row, or None if a row with same external_message_id
    already exists (UNIQUE conflict).
    """
    pool = await get_pool()
    try:
        return await pool.fetchrow(
            """
            INSERT INTO messages (
                conversation_id, direction, text, media_url, media_type,
                source, scenario_id, claude_used, claude_model,
                claude_tokens_in, claude_tokens_out,
                safety_blocked, safety_reason,
                external_message_id, raw_payload
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15)
            RETURNING *
            """,
            conversation_id, direction, text, media_url, media_type,
            source, scenario_id, claude_used, claude_model,
            claude_tokens_in, claude_tokens_out,
            safety_blocked, safety_reason,
            external_message_id, raw_payload,
        )
    except asyncpg.UniqueViolationError:
        return None


async def get_recent(conversation_id: int, limit: int = 20) -> list[asyncpg.Record]:
    """Return recent messages in a conversation, oldest first."""
    pool = await get_pool()
    rows = await pool.fetch(
        """
        SELECT * FROM messages
        WHERE conversation_id = $1
        ORDER BY created_at DESC
        LIMIT $2
        """,
        conversation_id, limit,
    )
    return list(reversed(rows))
