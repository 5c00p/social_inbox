"""Repository for events_log table."""
from __future__ import annotations

from typing import Any

import asyncpg

from app.repos.pool import get_pool


async def insert(
    *,
    provider_name: str,
    platform: str | None,
    event_type: str,
    external_event_id: str | None,
    payload: dict[str, Any],
    signature_valid: bool,
) -> asyncpg.Record:
    """Insert a raw webhook event. Returns the inserted row.

    Raises asyncpg.UniqueViolationError if (provider_name, external_event_id)
    already exists — caller should treat this as duplicate and skip processing.
    """
    pool = await get_pool()
    return await pool.fetchrow(
        """
        INSERT INTO events_log (
            provider_name, platform, event_type,
            external_event_id, payload, signature_valid
        )
        VALUES ($1, $2, $3, $4, $5, $6)
        RETURNING *
        """,
        provider_name, platform, event_type,
        external_event_id, payload, signature_valid,
    )


async def mark_processed(event_id: int, error: str | None = None) -> None:
    """Mark event as processed. If error is provided — also store it."""
    pool = await get_pool()
    await pool.execute(
        """
        UPDATE events_log
        SET processed_at = NOW(), error = $2
        WHERE id = $1
        """,
        event_id, error,
    )


async def is_already_processed(
    provider_name: str,
    external_event_id: str,
) -> bool:
    """Check if this external event has already been processed (processed_at IS NOT NULL)."""
    pool = await get_pool()
    row = await pool.fetchrow(
        """
        SELECT processed_at FROM events_log
        WHERE provider_name = $1 AND external_event_id = $2
        """,
        provider_name, external_event_id,
    )
    return row is not None and row["processed_at"] is not None
