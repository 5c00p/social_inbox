"""Postgres connection pool and migration runner.

Pattern: same as bot_purify — raw SQL files in migrations/ are applied
in lexicographic order at startup. Each file is wrapped in a transaction.

Tracking applied migrations: a `_migrations` table records each filename
that was applied successfully. Re-running is idempotent.
"""
from __future__ import annotations

import json
from pathlib import Path

import asyncpg

from app.config import get_settings
from app.utils.logging import get_logger

log = get_logger(__name__)

_pool: asyncpg.Pool | None = None

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"


async def _init_connection(conn: asyncpg.Connection) -> None:
    """Register JSON/JSONB codecs so Python dicts are serialized automatically."""
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


async def get_pool() -> asyncpg.Pool:
    """Return the global pool, creating it on first call."""
    global _pool
    if _pool is None:
        settings = get_settings()
        _pool = await asyncpg.create_pool(
            dsn=settings.postgres_dsn,
            min_size=2,
            max_size=10,
            command_timeout=30,
            init=_init_connection,
        )
        log.info("postgres_pool_created", min_size=2, max_size=10)
    return _pool


async def close_pool() -> None:
    """Close the pool. Call on shutdown."""
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
        log.info("postgres_pool_closed")


async def ping() -> bool:
    """Return True if Postgres is reachable. Used by /ready endpoint."""
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            value = await conn.fetchval("SELECT 1")
            return bool(value == 1)
    except (asyncpg.PostgresError, OSError) as exc:
        log.warning("postgres_ping_failed", error=str(exc))
        return False


async def run_migrations() -> None:
    """Apply all migration files from migrations/ that have not yet been applied.

    Idempotent: running it twice on the same DB is a no-op.
    Each migration file runs inside a single transaction.
    Errors abort the migration and re-raise.
    """
    pool = await get_pool()

    async with pool.acquire() as conn:
        # Bootstrap migration tracking table.
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS _migrations (
                filename    TEXT PRIMARY KEY,
                applied_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )

        applied: set[str] = {
            r["filename"]
            for r in await conn.fetch("SELECT filename FROM _migrations")
        }

    files = sorted(MIGRATIONS_DIR.glob("*.sql"))
    if not files:
        log.warning("no_migration_files_found", path=str(MIGRATIONS_DIR))
        return

    for file in files:
        if file.name in applied:
            log.debug("migration_skipped", filename=file.name)
            continue

        sql = file.read_text(encoding="utf-8")
        log.info("migration_applying", filename=file.name, size_bytes=len(sql))

        async with pool.acquire() as conn, conn.transaction():
            await conn.execute(sql)
            await conn.execute(
                "INSERT INTO _migrations (filename) VALUES ($1)",
                file.name,
            )

        log.info("migration_applied", filename=file.name)
