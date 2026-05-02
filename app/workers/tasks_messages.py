"""arq task: process a single IncomingEvent.

This is the SOLE consumer of the events queue.

What it does (in this task):
1. Idempotency check: if events_log.processed_at IS NOT NULL → skip
2. Find or create social_user
3. Find or create active conversation
4. Insert message with direction='in'
5. Bump social_users.last_message_at and conversations.last_message_at
6. Mark events_log row as processed

What it does NOT do (yet):
- Run scenarios (Task 07)
- Generate replies (Task 13 — Claude)
- Apply safety filters (Task 14)
- Send anything outbound

The full pipeline grows from this scaffold in subsequent tasks.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.models.events import IncomingEvent
from app.repos import conversations, messages, users
from app.repos import events as events_repo
from app.utils.logging import get_logger

log = get_logger(__name__)


async def process_incoming_event(
    ctx: dict[str, Any],
    event_dict: dict[str, Any],
    log_id: int,
) -> None:
    """Process a single event from the queue.

    Args:
        ctx: arq job context (unused for now, available for DB pool reuse later)
        event_dict: serialized IncomingEvent (model_dump(mode='json'))
        log_id: id of the events_log row to mark as processed at the end
    """
    event = IncomingEvent.model_validate(event_dict)

    # 1. Idempotency check — was this event_id already processed?
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
        # 2. Find or create social_user
        user = await users.get_by_external(
            event.provider, event.platform, event.external_user_id,
        )
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

        # 3. Find or create active conversation
        conv = await conversations.get_or_create(user["id"], event.platform)

        # 4. Insert message
        msg = await messages.insert(
            conversation_id=conv["id"],
            direction="in",
            text=event.text,
            media_url=event.media_url,
            source=_source_from_event_type(event.event_type),
            external_message_id=event.external_event_id,
            raw_payload=event.raw_payload,
        )
        if msg is None:
            log.info(
                "message_skipped_duplicate",
                external_event_id=event.external_event_id,
            )

        # 5. Bump timestamps
        await users.update_last_message_at(user["id"], event.occurred_at)
        await conversations.update_last_message_at(conv["id"], event.occurred_at)

        # 6. Mark event processed (success)
        await events_repo.mark_processed(log_id, error=None)

        log.info("event_processed_ok", log_id=log_id, user_id=user["id"], conv_id=conv["id"])
    except Exception as exc:
        log.exception("event_processing_failed", log_id=log_id, error=str(exc))
        await events_repo.mark_processed(log_id, error=str(exc)[:500])
        raise  # arq retries with exponential backoff


def _source_from_event_type(event_type: str) -> str:
    """Map EventType to messages.source value."""
    return {
        "message": "dm",
        "comment": "comment",
        "postback": "postback",
    }.get(event_type, "unknown")
