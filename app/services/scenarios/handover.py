"""Handover scenario — explicit user request for human operator.

Triggered by keyword like "оператор", "человек", "agent" (configured in DB).
Unlike Claude tool-use escalation (which silently flips status with no reply),
this scenario sends a polite ack to the user so they know help is coming.

Behavior:
1. Trigger handover (status flip + admin notification)
2. Send polite acknowledgement: "Передаю Юле, она ответит в течение..."
"""
from __future__ import annotations

import asyncpg

from app.models.events import IncomingEvent, OutgoingMessage
from app.services import handover
from app.services.scenario_engine import register_scenario
from app.utils.logging import get_logger

log = get_logger(__name__)


@register_scenario("handover")
async def handle_handover(
    event: IncomingEvent,
    user: asyncpg.Record,
    conversation: asyncpg.Record,
    scenario: asyncpg.Record,
) -> OutgoingMessage | None:
    template = scenario["template"] or (
        "Хорошо! Передаю Юле, она ответит лично в течение нескольких часов 💚"
    )

    # Trigger handover BEFORE sending — even if send fails, conversation
    # is still flagged for Yulia in admin.
    await handover.trigger_handover(
        conversation=conversation,
        user=user,
        source="operator_request",
        reason=event.text or "(empty user message)",
    )

    return OutgoingMessage(
        platform=event.platform,
        external_user_id=event.external_user_id,
        text=template,
        scenario_id=scenario["id"],
    )
