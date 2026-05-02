"""Echo scenario — minimal working scenario for end-to-end testing.

Replies with "Получено: <text>". Used in tests and as a catch-all fallback
in early development. To be replaced by FAQ/Smart in Task 13.

Registered as type='echo'.
"""
from __future__ import annotations

import asyncpg

from app.models.events import IncomingEvent, OutgoingMessage
from app.services.scenario_engine import register_scenario


@register_scenario("echo")
async def handle_echo(
    event: IncomingEvent,
    user: asyncpg.Record,
    conversation: asyncpg.Record,
    scenario: asyncpg.Record,
) -> OutgoingMessage | None:
    text_in = event.text or ""
    reply = f"Получено: {text_in[:200]}"
    return OutgoingMessage(
        platform=event.platform,
        external_user_id=event.external_user_id,
        text=reply,
        scenario_id=scenario["id"],
    )
