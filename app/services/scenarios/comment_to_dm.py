"""Comment-to-DM scenario.

Triggered by ScenarioEngine when a user posts a comment with a matching keyword
under one of Yulia's Reels/posts. Sends a private reply (DM) tied to that comment,
with a Telegram deep-link, and optionally a public reply on the comment itself.

Idempotency:
- Per-(user, post, scenario) — exactly one DM even if user posts 5 comments
- Implemented via comment_user_dedup table (migration 008)

Re-uses welcome's lifetime flag in lead_tracker.was_welcome_sent / mark_welcome_sent.
Reasoning: welcome and comment-to-DM are both "first-touch lead magnet". If the
user got a welcome-via-DM yesterday and posts a comment today, we don't want
another deep-link DM — they already have the link. The comment is acknowledged
through the optional public reply.
"""
from __future__ import annotations

from typing import Any

import asyncpg

from app.models.events import IncomingEvent, OutgoingMessage, QuickReply
from app.repos import comment_triggers as ct_repo
from app.services import lead_tracker
from app.services.scenario_engine import register_scenario
from app.services.scenarios.welcome import DEFAULT_FIRST_NAME, DISCLAIMER, _extract_first_name
from app.utils.logging import get_logger

log = get_logger(__name__)


@register_scenario("comment_to_dm")
async def handle_comment_to_dm(
    event: IncomingEvent,
    user: asyncpg.Record,
    conversation: asyncpg.Record,
    scenario: asyncpg.Record,
) -> OutgoingMessage | None:
    # Sanity: this scenario only fires on comments
    if event.event_type != "comment":
        log.warning(
            "comment_to_dm_called_for_non_comment",
            event_type=event.event_type,
            user_id=user["id"],
        )
        return None

    if not event.comment_id or not event.post_id:
        log.warning(
            "comment_to_dm_missing_ids",
            user_id=user["id"],
            comment_id=event.comment_id,
            post_id=event.post_id,
        )
        return None

    # 1. Per-(user, post, scenario) idempotency
    if await ct_repo.already_replied(
        user_id=user["id"],
        platform=event.platform,
        post_id=event.post_id,
        scenario_id=scenario["id"],
    ):
        log.info(
            "comment_to_dm_skipped_already_replied",
            user_id=user["id"],
            post_id=event.post_id,
            scenario_id=scenario["id"],
        )
        return None

    # 2. Lifetime welcome-flag (re-use, see module docstring rationale)
    if await lead_tracker.was_welcome_sent(user["id"]):
        # Mark deduped anyway, to prevent the engine from retrying this comment
        await ct_repo.mark_replied(
            user_id=user["id"],
            platform=event.platform,
            post_id=event.post_id,
            scenario_id=scenario["id"],
        )
        log.info(
            "comment_to_dm_skipped_user_already_received_welcome",
            user_id=user["id"],
        )
        return None

    # 3. Resolve scenario_slug
    metadata = dict(scenario["metadata"]) if scenario["metadata"] else {}
    scenario_slug = metadata.get("tg_scenario_slug", "purify")

    # 4. Build deep-link
    try:
        tg_link = lead_tracker.build_deep_link(user["short_id"], scenario_slug)
    except ValueError as exc:
        log.error(
            "comment_to_dm_invalid_slug",
            scenario_id=scenario["id"],
            slug=scenario_slug,
            error=str(exc),
        )
        return None

    # 5. Resolve template
    first_name = _extract_first_name(user["full_name"]) or DEFAULT_FIRST_NAME
    template = scenario["template"] or ""
    text = template.format(
        first_name=first_name,
        tg_link=tg_link,
        disclaimer=DISCLAIMER,
    )

    # 6. Quick replies
    quick_replies = _build_quick_replies(metadata, tg_link)

    # 7. Mark deduped + welcome-sent BEFORE returning, same logic as welcome.py
    await ct_repo.mark_replied(
        user_id=user["id"],
        platform=event.platform,
        post_id=event.post_id,
        scenario_id=scenario["id"],
    )
    await lead_tracker.mark_welcome_sent(user["id"])

    # TODO(Task 05): if metadata.get('public_reply_text'), call
    # provider.reply_to_comment(event.comment_id, metadata['public_reply_text']).
    # Requires extending MessagingProvider ABC with reply_to_comment method
    # and implementing it in SendPulseProvider.
    public_reply = metadata.get("public_reply_text")
    if public_reply:
        log.info(
            "public_reply_pending_task_05",
            comment_id=event.comment_id,
            text=public_reply,
        )

    log.info(
        "comment_to_dm_built",
        user_id=user["id"],
        short_id=user["short_id"],
        post_id=event.post_id,
        comment_id=event.comment_id,
        slug=scenario_slug,
    )

    return OutgoingMessage(
        platform=event.platform,
        external_user_id=event.external_user_id,
        text=text,
        quick_replies=quick_replies,
        reply_to_comment_id=event.comment_id,
        scenario_id=scenario["id"],
    )


def _build_quick_replies(
    metadata: dict[str, Any],
    tg_link: str,
) -> list[QuickReply] | None:
    """Same shape as welcome._build_quick_replies but kept local to this module
    to allow independent evolution of comment-to-DM UX.
    """
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
            log.warning("quick_reply_invalid", error=str(exc))
            continue
    return out or None
