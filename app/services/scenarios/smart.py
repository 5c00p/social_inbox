"""Smart scenario — Claude-powered reply for messages without keyword matches.

Replaces echo as the engine fallback. Triggered by ScenarioEngine when:
- Conversation is active (not in handover)
- Event is event_type='message' (NOT comments — those go to comment-to-DM)
- No keyword matched
- User is not brand-new (new users get welcome instead)

Behavior:
- Calls claude_responder.respond() with conversation context
- If Claude returns text → wrap into OutgoingMessage and return
- If Claude requested escalation → set conversation status to handover_pending
  and return None (no reply sent — Yulia will handle)
- If Claude returned None (budget/error/empty) → return None (silent skip)

Note on safety:
This handler does NOT yet apply doTERRA banned-pattern filters to the response.
That layer is added in Task 14 — banned_patterns check between claude_responder
and the OutgoingMessage construction.
"""
from __future__ import annotations

import asyncpg

from app.models.events import IncomingEvent, OutgoingMessage
from app.repos import conversations as conversations_repo
from app.services import claude_responder
from app.services.scenario_engine import register_scenario
from app.utils.logging import get_logger

log = get_logger(__name__)


@register_scenario("smart")
async def handle_smart(
    event: IncomingEvent,
    user: asyncpg.Record,
    conversation: asyncpg.Record,
    scenario: asyncpg.Record,
) -> OutgoingMessage | None:
    if not event.text:
        log.info("smart_skipped_empty_text", user_id=user["id"])
        return None

    # Check user-level smart_mode flag (set by admin to disable AI for VIP/problematic accounts)
    if not user["smart_mode_enabled"]:
        log.info("smart_skipped_user_smart_disabled", user_id=user["id"])
        return None

    # Allow scenario row to override default model via metadata
    metadata = dict(scenario["metadata"]) if scenario["metadata"] else {}
    model: str | None = metadata.get("claude_model")  # None → use settings default

    reply = await claude_responder.respond(
        user_id=user["id"],
        conversation_id=conversation["id"],
        incoming_text=event.text,
        model=model,
    )

    if reply is None:
        # Budget exceeded / API error / empty — silently skip
        return None

    if reply.escalation:
        # Claude wants a human — flip status, do not send anything to user
        await conversations_repo.set_status(
            conversation["id"],
            "handover_pending",
            reason=f"claude: {reply.escalation_reason}",
        )
        log.info(
            "smart_escalated_to_human",
            user_id=user["id"],
            conv_id=conversation["id"],
            reason=reply.escalation_reason,
        )
        return None

    return OutgoingMessage(
        platform=event.platform,
        external_user_id=event.external_user_id,
        text=reply.text,
        scenario_id=scenario["id"],
        claude_metadata={
            "model": reply.model,
            "tokens_in": reply.tokens_in,
            "tokens_out": reply.tokens_out,
        },
    )
