"""Service for tracking the journey from social DM to Telegram bot.

Responsibilities:
- Build Telegram deep-link URLs in the format:
    https://t.me/{bot_username}?start=ig_{short_id}_{scenario_slug}
- Mark welcome-sent flag in Redis (lifetime idempotency)
- Record handover when bot_purify confirms the lead arrived (Task 11)

Deep-link payload format:
    ig_<short_id>_<scenario_slug>

Where:
- 'ig'           - prefix indicating Instagram/social_inbox origin
                   (matches the parser in bot_purify/handlers/start.py)
- short_id       - 8 chars alphanumeric, no '_' or '-' (see app/utils/short_id.py)
- scenario_slug  - lowercase ASCII identifier, no '_' inside
                   (e.g. 'purify', 'oils', 'faq')

Why no '_' inside scenario_slug:
    bot_purify parser splits payload on FIRST underscore after 'ig_':
    "ig_abc123_purify"      -> short_id='abc123', scenario='purify'
    "ig_abc123_purify_v2"   -> short_id='abc123', scenario='purify_v2'  (still works)
    But avoid leading underscores in slug to keep splitting predictable.
"""
from __future__ import annotations

import re
from datetime import UTC, datetime

from app.config import get_settings
from app.repos import users
from app.repos.redis_client import get_redis
from app.utils.logging import get_logger

log = get_logger(__name__)

# Welcome-sent flag TTL — long enough that lifetime-once is effectively guaranteed,
# but bounded so Redis isn't infinitely growing. 180 days = 6 months > typical user
# lifecycle in our funnel. After expiry, a user re-engaging would get welcome again,
# which is acceptable.
WELCOME_TTL_SECONDS = 60 * 60 * 24 * 180

_SLUG_RE = re.compile(r"^[a-z0-9]+(?:[-][a-z0-9]+)*$")


def _validate_slug(slug: str) -> None:
    """Raise ValueError if slug contains forbidden characters."""
    if not _SLUG_RE.match(slug):
        raise ValueError(
            f"Invalid scenario_slug {slug!r}: must be lowercase alphanumeric, "
            f"optional '-' separator, no '_' allowed"
        )


def build_deep_link(short_id: str, scenario_slug: str = "purify") -> str:
    """Build the full Telegram deep-link URL.

    Examples:
        build_deep_link('Kd7nQ2x9')         -> https://t.me/yuliya_purify_bot?start=ig_Kd7nQ2x9_purify
        build_deep_link('Kd7nQ2x9', 'oils') -> https://t.me/yuliya_purify_bot?start=ig_Kd7nQ2x9_oils
    """
    _validate_slug(scenario_slug)
    settings = get_settings()
    return (
        f"https://t.me/{settings.telegram_bot_username}"
        f"?start=ig_{short_id}_{scenario_slug}"
    )


# ---- Welcome lifetime idempotency ----

def _welcome_key(user_id: int) -> str:
    return f"welcome:sent:{user_id}"


async def was_welcome_sent(user_id: int) -> bool:
    """Return True if welcome was already sent to this user within TTL."""
    redis = await get_redis()
    return bool(await redis.exists(_welcome_key(user_id)))


async def mark_welcome_sent(user_id: int) -> None:
    """Set the welcome-sent flag with a 180-day TTL."""
    redis = await get_redis()
    await redis.set(
        _welcome_key(user_id),
        datetime.now(UTC).isoformat(),
        ex=WELCOME_TTL_SECONDS,
    )


# ---- Handover recording (used by Task 11) ----

async def record_handover(user_id: int, tg_user_id: int) -> None:
    """Record that the user successfully landed in the Telegram bot."""
    await users.mark_handover(user_id, tg_user_id, datetime.now(UTC))
    log.info("handover_recorded", user_id=user_id, tg_user_id=tg_user_id)
