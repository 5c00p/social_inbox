"""Welcome scenario — first contact greeting with Telegram deep-link.

Triggered by ScenarioEngine when a brand-new user sends their first DM
(or when a keyword explicitly maps to a welcome-typed scenario).

Behavior:
- Look up user's short_id (must already exist — created by worker before engine call)
- Build deep-link URL using scenario.metadata.tg_scenario_slug
- Resolve {first_name}, {tg_link}, {disclaimer} placeholders in template
- Build quick_replies from scenario.metadata.quick_replies
- Mark welcome-sent flag in Redis to prevent re-sending

Lifetime guarantee:
- Each user receives welcome at most once per WELCOME_TTL_SECONDS (180 days)
- If welcome was already sent, this scenario returns None (silent skip)
"""
from __future__ import annotations

from typing import Any

import asyncpg

from app.models.events import IncomingEvent, OutgoingMessage, QuickReply
from app.services import lead_tracker
from app.services.scenario_engine import register_scenario
from app.utils.logging import get_logger

log = get_logger(__name__)

# Disclaimer text — required by:
# - Meta App Review (when we eventually migrate to direct Meta integration)
# - doTERRA compliance (clarifies it's an automated assistant, not Yulia personally)
# - User trust (users dislike covert automation)
DISCLAIMER = (
    "ℹ️ Это автоматический помощник. "
    "Чтобы написать Юле напрямую — напиши в ответ слово «оператор»."
)

DEFAULT_FIRST_NAME = "дорогая"


@register_scenario("welcome")
async def handle_welcome(
    event: IncomingEvent,
    user: asyncpg.Record,
    conversation: asyncpg.Record,
    scenario: asyncpg.Record,
) -> OutgoingMessage | None:
    # 1. Lifetime idempotency
    if await lead_tracker.was_welcome_sent(user["id"]):
        log.info(
            "welcome_skipped_already_sent",
            user_id=user["id"],
            short_id=user["short_id"],
        )
        return None

    # 2. Resolve scenario_slug from metadata (default 'purify')
    metadata = dict(scenario["metadata"]) if scenario["metadata"] else {}
    scenario_slug = metadata.get("tg_scenario_slug", "purify")

    # 3. Build deep-link
    try:
        tg_link = lead_tracker.build_deep_link(user["short_id"], scenario_slug)
    except ValueError as exc:
        log.error(
            "welcome_invalid_slug",
            scenario_id=scenario["id"],
            slug=scenario_slug,
            error=str(exc),
        )
        return None

    # 4. Resolve placeholders in template
    first_name = _extract_first_name(user["full_name"]) or DEFAULT_FIRST_NAME
    template = scenario["template"] or ""
    text = template.format(
        first_name=first_name,
        tg_link=tg_link,
        disclaimer=DISCLAIMER,
    )

    # 5. Build quick_replies from metadata (resolve {tg_link} placeholder)
    quick_replies = _build_quick_replies(metadata, tg_link)

    # 6. Mark welcome-sent BEFORE returning the message.
    # Worker sends it; we mark idempotency early so retry won't double-send.
    await lead_tracker.mark_welcome_sent(user["id"])

    log.info(
        "welcome_built",
        user_id=user["id"],
        short_id=user["short_id"],
        slug=scenario_slug,
    )

    return OutgoingMessage(
        platform=event.platform,
        external_user_id=event.external_user_id,
        text=text,
        quick_replies=quick_replies,
        scenario_id=scenario["id"],
    )


def _extract_first_name(full_name: str | None) -> str | None:
    """Take first whitespace-separated word from full_name. Returns None if empty."""
    if not full_name:
        return None
    parts = full_name.strip().split()
    return parts[0] if parts else None


def _build_quick_replies(
    metadata: dict[str, Any],
    tg_link: str,
) -> list[QuickReply] | None:
    """Build QuickReply list from scenario metadata, resolving {tg_link} placeholder."""
    raw = metadata.get("quick_replies")
    if not raw or not isinstance(raw, list):
        return None

    out: list[QuickReply] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        title = item.get("title")
        payload = item.get("payload")
        if not title or not payload:
            continue
        resolved_payload = payload.replace("{tg_link}", tg_link)
        try:
            out.append(QuickReply(title=title, payload=resolved_payload))
        except Exception as exc:
            log.warning(
                "quick_reply_invalid",
                title=title,
                payload=payload,
                error=str(exc),
            )
            continue

    return out or None
