"""Admin-side database helpers.

Why a separate module from app.repos.pool: Streamlit reruns each page render
in a fresh asyncio event loop via asyncio.run(...). The application's
singleton pool (app/repos/pool.py:get_pool) is bound to whatever loop it
was first created in; subsequent reruns find the cached pool referencing
a closed loop and fail with `RuntimeError: Event loop is closed`.

Strategy: each top-level admin coroutine opens a fresh asyncpg connection
via asyncpg.connect(), runs one query, then closes it. No pool, no caching.
The admin UI is single-user and low-frequency, so the extra ~few-ms connect
cost per page render is negligible compared to Streamlit's own rerender.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import asyncpg

from app.config import get_settings


async def _init_codecs(conn: asyncpg.Connection) -> None:
    """Mirror app.repos.pool._init_connection so JSONB rows come back as dicts."""
    await conn.set_type_codec(
        "jsonb",
        encoder=json.dumps,
        decoder=json.loads,
        schema="pg_catalog",
        format="text",
    )
    await conn.set_type_codec(
        "json",
        encoder=json.dumps,
        decoder=json.loads,
        schema="pg_catalog",
        format="text",
    )


@asynccontextmanager
async def get_conn() -> AsyncIterator[asyncpg.Connection]:
    """Open a fresh connection for the duration of one admin query.

    Each call opens its own connection bound to the current event loop —
    safe across Streamlit reruns. Connection is closed on exit even if the
    body raises.
    """
    settings = get_settings()
    conn = await asyncpg.connect(dsn=settings.postgres_dsn)
    try:
        await _init_codecs(conn)
        yield conn
    finally:
        await conn.close()


async def fetch(query: str, *args: Any) -> list[asyncpg.Record]:
    async with get_conn() as conn:
        rows: list[asyncpg.Record] = await conn.fetch(query, *args)
        return rows


async def fetchrow(query: str, *args: Any) -> asyncpg.Record | None:
    async with get_conn() as conn:
        row: asyncpg.Record | None = await conn.fetchrow(query, *args)
        return row


async def fetchval(query: str, *args: Any) -> Any:
    async with get_conn() as conn:
        return await conn.fetchval(query, *args)


async def execute(query: str, *args: Any) -> str:
    async with get_conn() as conn:
        status: str = await conn.execute(query, *args)
        return status
