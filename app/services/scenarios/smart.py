"""Smart scenario — Claude-powered reply for messages without keyword matches.

Replaces echo as the engine fallback. Triggered by ScenarioEngine when:
- Conversation is active (not in handover)
- Event is event_type='message' (NOT comments — those go to comment-to-DM)
- No keyword matched
- User is not brand-new (new users get welcome instead)

Behavior:
- Calls claude_responder.respond() with conversation context
- If Claude returns text → runs check_outgoing safety filter
  - blocked → audit row + handover_pending, no message sent
  - ok → wrap into OutgoingMessage and return
- If Claude requested escalation → trigger_handover + return None
- If Claude returned None (budget/error/empty) → return None (silent skip)
"""
from __future__ import annotations

import asyncpg

from app.models.events import IncomingEvent, OutgoingMessage
from app.repos import messages as messages_repo
from app.services import claude_responder, handover, safety
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
        # Claude wants a human — flip status via handover service, do not send to user
        await handover.trigger_handover(
            conversation=conversation,
            user=user,
            source="claude_tool_use",
            reason=reply.escalation_reason or "no reason given",
        )
        log.info(
            "smart_escalated_to_human",
            user_id=user["id"],
            conv_id=conversation["id"],
            reason=reply.escalation_reason,
        )
        return None

    # Outgoing safety check on Claude's reply
    if reply.text:
        safety_result = safety.check_outgoing(reply.text)
        if safety_result.verdict == "blocked":
            log.warning(
                "smart_blocked_by_safety",
                user_id=user["id"],
                reason=safety_result.reason,
                text_preview=reply.text[:120],
            )
            # Persist audit row (safety_blocked=True, text=None — never delivered)
            await messages_repo.insert(
                conversation_id=conversation["id"],
                direction="out",
                text=None,
                source="reply",
                scenario_id=scenario["id"],
                claude_used=True,
                claude_model=reply.model,
                claude_tokens_in=reply.tokens_in,
                claude_tokens_out=reply.tokens_out,
                safety_blocked=True,
                safety_reason=safety_result.reason,
                external_message_id=f"blocked:{user['id']}:{reply.tokens_out}",
            )
            await handover.trigger_handover(
                conversation=conversation,
                user=user,
                source="outgoing_safety_block",
                reason=safety_result.reason or "unknown",
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
