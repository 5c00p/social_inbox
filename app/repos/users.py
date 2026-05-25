"""Repository for social_users table.

Style: raw SQL via asyncpg (matches bot_purify), no ORM.
All queries respect soft-delete (filter deleted_at IS NULL by default).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import asyncpg

from app.models.enums import Platform, ProviderName
from app.repos.pool import get_pool
from app.utils.short_id import make_short_id


async def get_by_external(
    provider_name: ProviderName,
    platform: Platform,
    external_id: str,
) -> asyncpg.Record | None:
    """Return user by (provider, platform, external_id) or None."""
    pool = await get_pool()
    return await pool.fetchrow(
        """
        SELECT * FROM social_users
        WHERE provider_name = $1
          AND platform = $2
          AND external_id = $3
          AND deleted_at IS NULL
        """,
        provider_name,
        platform,
        external_id,
    )


async def get_by_short_id(short_id: str) -> asyncpg.Record | None:
    """Return user by short_id or None. Used by /api/lead/{short_id}."""
    pool = await get_pool()
    return await pool.fetchrow(
        """
        SELECT * FROM social_users
        WHERE short_id = $1
          AND deleted_at IS NULL
        """,
        short_id,
    )


async def create(
    *,
    provider_name: ProviderName,
    platform: Platform,
    external_id: str,
    username: str | None = None,
    full_name: str | None = None,
    profile_pic_url: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> asyncpg.Record:
    """Create a new user. Generates short_id internally.

    Caller MUST check via get_by_external() first to avoid duplicates;
    this method does NOT do upsert. (Race conditions are acceptable here —
    UNIQUE (provider_name, platform, external_id) will protect us with IntegrityError.)
    """
    pool = await get_pool()
    short_id = make_short_id()
    return await pool.fetchrow(
        """
        INSERT INTO social_users (
            provider_name, platform, external_id,
            username, full_name, profile_pic_url,
            short_id, metadata
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
        RETURNING *
        """,
        provider_name,
        platform,
        external_id,
        username,
        full_name,
        profile_pic_url,
        short_id,
        metadata or {},
    )


async def update_last_message_at(user_id: int, ts: datetime) -> None:
    """Bump last_message_at. Called on every incoming message."""
    pool = await get_pool()
    await pool.execute(
        "UPDATE social_users SET last_message_at = $2 WHERE id = $1",
        user_id,
        ts,
    )


async def mark_handover(user_id: int, tg_user_id: int, ts: datetime) -> None:
    """Record successful handover to Telegram bot."""
    pool = await get_pool()
    await pool.execute(
        """
        UPDATE social_users
        SET tg_handover_at = $2, tg_user_id = $3
        WHERE id = $1
        """,
        user_id,
        ts,
        tg_user_id,
    )


async def soft_delete(user_id: int, ts: datetime) -> None:
    """Mark user as deleted. Physical deletion happens after 30 days (Task 14)."""
    pool = await get_pool()
    await pool.execute(
        "UPDATE social_users SET deleted_at = $2 WHERE id = $1",
        user_id,
        ts,
    )


async def get_last_outgoing_with_scenario(user_id: int) -> asyncpg.Record | None:
    """Return the most recent OUT message together with its scenario metadata.

    Used by /api/lead/{short_id} to determine which scenario_slug brought
    the user to Telegram (the deep-link in this message contains it).
    """
    pool = await get_pool()
    return await pool.fetchrow(
        """
        SELECT m.id AS message_id,
               m.created_at,
               s.metadata AS scenario_metadata,
               s.name AS scenario_name
        FROM messages m
        JOIN conversations c ON c.id = m.conversation_id
        LEFT JOIN scenarios s ON s.id = m.scenario_id
        WHERE c.user_id = $1
          AND m.direction = 'out'
        ORDER BY m.created_at DESC
        LIMIT 1
        """,
        user_id,
    )
