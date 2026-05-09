"""arq task: process a single IncomingEvent.

Pipeline (post-Task 07):
1. Idempotency: if events_log.processed_at already set → skip
2. Find or create social_user (track is_new_user)
3. Find or create active conversation
4. Insert incoming message (direction='in')
5. Bump last_message_at on user and conversation
6. Run ScenarioEngine.handle() → maybe OutgoingMessage
7. If reply produced AND rate limit allows AND not blocked by safety:
    a. provider.send(OutgoingMessage) → external_message_id
    b. Insert outgoing message (direction='out')
8. Mark events_log row as processed

Future expansions (later tasks):
- Task 13: Claude-based smart scenarios + tool use for handover
- Task 14: safety filters on outgoing messages
- Task 14: per-user daily/lifetime rate limits
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import app.services.scenarios  # noqa: F401 — triggers @register_scenario side-effects
from app.models.events import IncomingEvent, OutgoingMessage
from app.providers import get_provider
from app.repos import conversations, messages, users
from app.repos import events as events_repo
from app.services import handover, safety, scenario_engine
from app.services.rate_limiter import can_reply, can_reply_daily
from app.utils.logging import get_logger

log = get_logger(__name__)


async def process_incoming_event(
    ctx: dict[str, Any],
    event_dict: dict[str, Any],
    log_id: int,
) -> None:
    event = IncomingEvent.model_validate(event_dict)

    # 1. Idempotency
    if await events_repo.is_already_processed(event.provider, event.external_event_id):
        log.info(
            "event_skipped_already_processed",
            external_event_id=event.external_event_id,
            log_id=log_id,
        )
        return

    age_seconds = (datetime.now(UTC) - event.occurred_at).total_seconds()
    log.info(
        "event_processing",
        external_event_id=event.external_event_id,
        platform=event.platform,
        event_type=event.event_type,
        age_seconds=int(age_seconds),
    )

    try:
        # 2. User
        user = await users.get_by_external(
            event.provider, event.platform, event.external_user_id,
        )
        is_new_user = user is None
        if user is None:
            user = await users.create(
                provider_name=event.provider,
                platform=event.platform,
                external_id=event.external_user_id,
                username=event.username,
                full_name=event.full_name,
            )
            log.info(
                "user_created",
                user_id=user["id"],
                short_id=user["short_id"],
                external_id=event.external_user_id,
            )

        # 3. Conversation
        conv = await conversations.get_or_create(user["id"], event.platform)

        # 4. Insert incoming
        await messages.insert(
            conversation_id=conv["id"],
            direction="in",
            text=event.text,
            media_url=event.media_url,
            source=_source_from_event_type(event.event_type),
            external_message_id=event.external_event_id,
            raw_payload=event.raw_payload,
        )

        # 5. Bump timestamps
        await users.update_last_message_at(user["id"], event.occurred_at)
        await conversations.update_last_message_at(conv["id"], event.occurred_at)

        # 5b. Pre-emptive safety triage on incoming DMs
        # (skipped for comments — symptom/operator keyword in a public Reels comment
        #  is unusual; comment-to-DM scenario handles comments normally)
        if event.event_type == "message" and event.text:
            safety_check = safety.check_incoming(event.text)
            if safety_check.trigger == "symptom":
                log.info(
                    "incoming_symptom_detected",
                    user_id=user["id"],
                    matched=safety_check.matched_text,
                )
                await handover.trigger_handover(
                    conversation=conv,
                    user=user,
                    source="symptom_detected",
                    reason=f"matched: {safety_check.matched_text}",
                )
                # Skip scenario engine — Yulia takes over
                await events_repo.mark_processed(log_id, error=None)
                log.info("event_processed_ok", log_id=log_id, user_id=user["id"], conv_id=conv["id"])
                return
            # operator_request goes through scenario engine normally:
            # keyword "оператор" → handover scenario → polite ack sent to user

        # 6. Scenario engine
        outgoing = await scenario_engine.handle(event, user, conv, is_new_user=is_new_user)

        # 7. Send reply if produced
        if outgoing is not None:
            await _send_and_record(outgoing, conv["id"], user["id"])

        # 8. Mark processed
        await events_repo.mark_processed(log_id, error=None)
        log.info("event_processed_ok", log_id=log_id, user_id=user["id"], conv_id=conv["id"])

    except Exception as exc:
        log.exception("event_processing_failed", log_id=log_id, error=str(exc))
        await events_repo.mark_processed(log_id, error=str(exc)[:500])
        raise


async def _send_and_record(
    outgoing: OutgoingMessage,
    conversation_id: int,
    user_id: int,
) -> None:
    """Send outbound message via provider and record it in messages table."""
    if not await can_reply(user_id):
        log.warning("reply_throttled_per_minute", user_id=user_id)
        return
    if not await can_reply_daily(user_id):
        log.warning("reply_throttled_per_day", user_id=user_id)
        return

    provider = get_provider()
    try:
        external_id = await provider.send(outgoing)
    except Exception as exc:
        log.exception("provider_send_failed", user_id=user_id, error=str(exc))
        external_id = None

    # If provider didn't return an ID, generate a local one to satisfy UNIQUE constraint.
    record_external_id = external_id if external_id else f"local:{uuid.uuid4()}"

    cm = outgoing.claude_metadata or {}
    await messages.insert(
        conversation_id=conversation_id,
        direction="out",
        text=outgoing.text,
        media_url=outgoing.media_url,
        source="reply",
        scenario_id=outgoing.scenario_id,
        claude_used=bool(cm),
        claude_model=cm.get("model"),
        claude_tokens_in=cm.get("tokens_in"),
        claude_tokens_out=cm.get("tokens_out"),
        external_message_id=record_external_id,
    )

    log.info(
        "outgoing_sent",
        user_id=user_id,
        scenario_id=outgoing.scenario_id,
        send_ok=external_id is not None,
    )


def _source_from_event_type(event_type: str) -> str:
    return {
        "message": "dm",
        "comment": "comment",
        "postback": "postback",
    }.get(event_type, "unknown")
