"""Smoke tests for stats queries — they must not fail on empty DB."""
from __future__ import annotations

from typing import Any

from admin.data import stats


async def test_daily_new_leads_empty(db: Any) -> None:
    result = await stats.daily_new_leads(days=14)
    assert isinstance(result, list)


async def test_conversion_to_telegram_empty(db: Any) -> None:
    result = await stats.conversion_to_telegram(days=30)
    assert "total" in result
    assert "converted" in result
    assert isinstance(result["total"], int)


async def test_handover_breakdown_empty(db: Any) -> None:
    assert await stats.handover_breakdown(days=30) == []


async def test_claude_token_usage_empty(db: Any) -> None:
    assert await stats.claude_token_usage(days=7) == []
