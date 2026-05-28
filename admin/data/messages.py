"""Message access for admin dashboard."""

from __future__ import annotations

import asyncpg

from admin.data import _db


async def get_messages(conversation_id: int, limit: int = 50) -> list[asyncpg.Record]:
    return await _db.fetch(
        """
        SELECT id, direction, text, source, scenario_id,
               claude_used, claude_model, claude_tokens_in, claude_tokens_out,
               safety_blocked, safety_reason, created_at
        FROM messages
        WHERE conversation_id = $1
        ORDER BY created_at ASC
        LIMIT $2
        """,
        conversation_id,
        limit,
    )
