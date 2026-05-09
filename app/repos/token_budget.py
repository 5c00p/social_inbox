"""Per-user-day Claude token budget tracking via Redis.

Limits (in line with CLAUDE.md §12.4 spirit, per-user defensive budget):
- input tokens: 50,000 per UTC day
- output tokens: 10,000 per UTC day

Why per-day, not per-month:
- Cheap protection against abuse (one user spamming long messages)
- Resets daily — doesn't permanently lock out a real user

Why Redis, not DB:
- High-frequency hot path (every Claude call)
- Auto-expiring keys at end of UTC day
- DB is the audit trail (messages.claude_tokens_in/out), Redis is the gate
"""
from __future__ import annotations

from datetime import UTC, datetime

from app.repos.redis_client import get_redis
from app.utils.logging import get_logger

log = get_logger(__name__)

INPUT_BUDGET_PER_DAY = 50_000
OUTPUT_BUDGET_PER_DAY = 10_000

# TTL: just over 24h to handle clock drift; key includes UTC date so old keys naturally don't collide.
KEY_TTL_SECONDS = 60 * 60 * 26


def _today_utc() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d")


def _input_key(user_id: int) -> str:
    return f"claude:budget:in:{user_id}:{_today_utc()}"


def _output_key(user_id: int) -> str:
    return f"claude:budget:out:{user_id}:{_today_utc()}"


async def can_call_claude(user_id: int) -> bool:
    """Return True if the user has not exceeded their daily token budget.

    Checked BEFORE issuing the API call. We can't know exact token counts in advance,
    but we know what's already been spent today.
    """
    redis = await get_redis()
    in_used = int(await redis.get(_input_key(user_id)) or 0)
    out_used = int(await redis.get(_output_key(user_id)) or 0)

    if in_used >= INPUT_BUDGET_PER_DAY or out_used >= OUTPUT_BUDGET_PER_DAY:
        log.warning(
            "claude_budget_exceeded",
            user_id=user_id,
            in_used=in_used,
            out_used=out_used,
            in_limit=INPUT_BUDGET_PER_DAY,
            out_limit=OUTPUT_BUDGET_PER_DAY,
        )
        return False
    return True


async def record_usage(user_id: int, tokens_in: int, tokens_out: int) -> None:
    """Increment the user's token counters. Called AFTER each Claude API call."""
    redis = await get_redis()
    pipe = redis.pipeline()
    pipe.incrby(_input_key(user_id), tokens_in)
    pipe.expire(_input_key(user_id), KEY_TTL_SECONDS)
    pipe.incrby(_output_key(user_id), tokens_out)
    pipe.expire(_output_key(user_id), KEY_TTL_SECONDS)
    await pipe.execute()
