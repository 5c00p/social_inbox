"""Tests for Claude token budget tracking."""
from __future__ import annotations

import pytest

from app.repos import token_budget
from app.repos.redis_client import get_redis


@pytest.fixture(autouse=True)
async def _clear_budget_keys() -> None:  # type: ignore[misc]
    redis = await get_redis()
    keys = await redis.keys("claude:budget:*")
    if keys:
        await redis.delete(*keys)
    yield  # type: ignore[misc]
    keys = await redis.keys("claude:budget:*")
    if keys:
        await redis.delete(*keys)


async def test_can_call_claude_when_no_usage() -> None:
    assert await token_budget.can_call_claude(99001) is True


async def test_record_usage_then_within_budget() -> None:
    await token_budget.record_usage(99002, tokens_in=1000, tokens_out=200)
    assert await token_budget.can_call_claude(99002) is True


async def test_input_budget_exhausted() -> None:
    await token_budget.record_usage(
        99003,
        tokens_in=token_budget.INPUT_BUDGET_PER_DAY,
        tokens_out=0,
    )
    assert await token_budget.can_call_claude(99003) is False


async def test_output_budget_exhausted() -> None:
    await token_budget.record_usage(
        99004,
        tokens_in=0,
        tokens_out=token_budget.OUTPUT_BUDGET_PER_DAY,
    )
    assert await token_budget.can_call_claude(99004) is False


async def test_other_user_budget_independent() -> None:
    await token_budget.record_usage(
        99005,
        tokens_in=token_budget.INPUT_BUDGET_PER_DAY,
        tokens_out=0,
    )
    assert await token_budget.can_call_claude(99005) is False
    # Different user is unaffected
    assert await token_budget.can_call_claude(99006) is True
