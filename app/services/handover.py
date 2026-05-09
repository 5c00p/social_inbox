"""Handover service — single point for transitioning a conversation
to handover_pending, with audit logging and admin notification.

Used by:
- safety.check_incoming → operator_request / symptom triggers
- smart scenario → Claude tool use 'escalate_to_human'
- safety check on outgoing Claude reply → banned pattern hit
- (future) admin manual handover from dashboard
"""
from __future__ import annotations

from typing import Literal

import asyncpg

from app.repos import conversations as conversations_repo
from app.services import notifications
from app.utils.logging import get_logger

log = get_logger(__name__)

HandoverSource = Literal[
    "operator_request",       # user wrote "оператор"
    "symptom_detected",       # incoming text matched symptom keyword
    "claude_tool_use",        # Claude requested escalate_to_human
    "outgoing_safety_block",  # Claude reply matched banned pattern
    "manual",                 # admin marked from dashboard (Task 15)
]


async def trigger_handover(
    *,
    conversation: asyncpg.Record,
    user: asyncpg.Record,
    source: HandoverSource,
    reason: str,
) -> None:
    """Transition a conversation to handover_pending and notify Yulia.

    Idempotent: calling twice on the same conversation is safe — status update
    is a simple UPDATE; second notification is sent (so Yulia sees the latest reason).
    """
    await conversations_repo.set_status(
        conversation["id"],
        "handover_pending",
        reason=f"{source}: {reason}",
    )
    log.info(
        "handover_triggered",
        conv_id=conversation["id"],
        user_id=user["id"],
        source=source,
        reason=reason,
    )

    # Best-effort admin notification
    msg = _format_admin_message(user=user, source=source, reason=reason)
    await notifications.notify_admin(msg)


def _format_admin_message(
    *,
    user: asyncpg.Record,
    source: HandoverSource,
    reason: str,
) -> str:
    """Format markdown text for admin Telegram notification."""
    username = user["username"] or "(no username)"
    full_name = user["full_name"] or "(no name)"
    platform = user["platform"]
    short_id = user["short_id"]

    source_label = {
        "operator_request": "👤 Запрос оператора",
        "symptom_detected": "⚠️ Симптомы / медицинский вопрос",
        "claude_tool_use": "🤖 Claude эскалировал",
        "outgoing_safety_block": "🛑 Заблокирован ответ (banned pattern)",
        "manual": "✋ Ручная эскалация",
    }.get(source, source)

    return (
        f"*{source_label}*\n\n"
        f"Платформа: `{platform}`\n"
        f"Пользователь: `@{username}` ({full_name})\n"
        f"short\\_id: `{short_id}`\n\n"
        f"*Причина:* {reason}\n\n"
        f"_Открой админку чтобы ответить._"
    )
