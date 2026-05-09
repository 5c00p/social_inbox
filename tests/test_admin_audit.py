"""Tests for admin audit log."""
from __future__ import annotations

from typing import Any

from admin.data import audit


async def test_record_and_recent(db: Any) -> None:
    await audit.record_action(
        actor="yulia",
        action="reply",
        target_type="conversation",
        target_id=42,
        details={"length": 100},
    )
    rows = await audit.recent(limit=10)
    target = next(r for r in rows if r["target_id"] == 42 and r["action"] == "reply")
    assert target["actor"] == "yulia"
    assert target["details"]["length"] == 100
