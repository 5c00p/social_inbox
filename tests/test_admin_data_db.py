"""Regression test for the admin '_db' helper.

Reproduces the original Streamlit bug:
    RuntimeError: Event loop is closed
    File "/app/admin/data/scenarios.py", line 11, in list_all
        return await pool.fetch(...)

Root cause: every Streamlit page rerun ran `asyncio.run(coro())`, and the
shared `app.repos.pool` singleton was bound to the FIRST loop. When that
loop was closed at the end of `asyncio.run`, the next page rerun tried to
acquire from a pool whose underlying connections referenced a closed loop.

Fix: `admin.data._db` opens a fresh `asyncpg.connect()` per call instead of
reusing the application-wide pool.

This test calls `asyncio.run()` TWICE on `_db.fetch(...)` — that is the
exact pattern that previously raised. If this passes, page reruns are safe.
"""

from __future__ import annotations

import asyncio

import pytest

from admin.data import _db


def test_db_fetch_safe_across_multiple_asyncio_run_calls(
    _db_setup: None,
) -> None:
    """Two sequential asyncio.run() calls must not fail with 'Event loop is closed'.

    Uses raw asyncio.run on purpose — pytest-asyncio's per-test loop would
    mask the bug. The fixture _db_setup ensures the DB schema is present.
    """
    first = asyncio.run(_db.fetchval("SELECT 1"))
    assert first == 1

    second = asyncio.run(_db.fetchval("SELECT 2"))
    assert second == 2


def test_db_fetch_returns_records(_db_setup: None) -> None:
    rows = asyncio.run(_db.fetch("SELECT 1 AS v UNION ALL SELECT 2 ORDER BY v"))
    assert [r["v"] for r in rows] == [1, 2]


def test_db_jsonb_codec_decodes_to_dict(_db_setup: None) -> None:
    """Verify the connection has the same JSONB codec as app.repos.pool."""
    row = asyncio.run(
        _db.fetchrow("SELECT '{\"a\": 1}'::jsonb AS payload"),
    )
    assert row is not None
    assert row["payload"] == {"a": 1}


@pytest.mark.asyncio
async def test_db_execute_returns_status_string(_db_setup: None) -> None:
    status = await _db.execute("SELECT 1")
    assert isinstance(status, str)
