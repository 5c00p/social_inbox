"""Track Claude API failure rate via Redis sliding window.

Why: Claude API can have hiccups (rate limits, regional outages). We want
to know when failures spike, not on first error. Sliding window approach:
- Increment failure counter on each error
- Increment success counter on each successful call
- After N attempts, check ratio. If failure_rate > threshold, alert.

Window: last 5 minutes. Sliding via Redis EXPIRE on counter keys.

Thresholds:
- min_attempts = 5  — don't alert on a single failure (could be transient)
- failure_rate_threshold = 0.5 — alert if 50%+ of recent calls fail
"""
from __future__ import annotations

from app.observability.alerts import fire_alert
from app.repos.redis_client import get_redis
from app.utils.logging import get_logger

log = get_logger(__name__)

WINDOW_SECONDS = 5 * 60
SUCCESS_KEY = "claude:health:success"
FAILURE_KEY = "claude:health:failure"

MIN_ATTEMPTS = 5
FAILURE_RATE_THRESHOLD = 0.5


async def record_success() -> None:
    redis = await get_redis()
    pipe = redis.pipeline()
    pipe.incr(SUCCESS_KEY)
    pipe.expire(SUCCESS_KEY, WINDOW_SECONDS)
    await pipe.execute()


async def record_failure() -> None:
    redis = await get_redis()
    pipe = redis.pipeline()
    pipe.incr(FAILURE_KEY)
    pipe.expire(FAILURE_KEY, WINDOW_SECONDS)
    await pipe.execute()
    await _check_threshold()


async def _check_threshold() -> None:
    redis = await get_redis()
    successes = int(await redis.get(SUCCESS_KEY) or 0)
    failures = int(await redis.get(FAILURE_KEY) or 0)
    total = successes + failures

    if total < MIN_ATTEMPTS:
        return

    failure_rate = failures / total
    if failure_rate >= FAILURE_RATE_THRESHOLD:
        await fire_alert(
            "claude_failures",
            f"Claude API failure rate: {failure_rate:.0%} "
            f"({failures}/{total} calls in last {WINDOW_SECONDS // 60} min). "
            f"Возможен сбой API или превышение rate-limit Anthropic.",
        )
