"""ScenarioEngine — routes incoming events to the correct scenario handler.

Architecture:
- Scenario handlers register themselves via @register_scenario('type_name')
- Engine.handle() looks at the event + DB context and chooses a handler
- Handler returns OutgoingMessage | None
- None means "no reply" (e.g. event was processed silently)

Routing logic (in Task 07):
1. If event_type='comment' AND keyword matched → run scenario from keyword.scenario_id
2. If event_type='message' AND keyword matched → run scenario from keyword.scenario_id
3. If event_type='message' AND user is brand new (first DM) → run default welcome
4. Otherwise → run echo (catch-all for testing in this task; replaced by FAQ/Smart in Task 13)

Future routing rules (Tasks 08+):
- Re-engagement (last seen >30 days ago) → re-engagement welcome
- "оператор" / "human" keyword → handover scenario
- Conversation in handover_pending → no auto-reply
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable

import asyncpg

from app.models.events import IncomingEvent, OutgoingMessage
from app.repos import scenarios as scenarios_repo
from app.services.keyword_matcher import KeywordContext, KeywordMatch
from app.services.keyword_matcher import match as match_keywords
from app.utils.logging import get_logger

log = get_logger(__name__)

# Handler signature: (event, user, conversation, scenario_row) -> OutgoingMessage | None
ScenarioHandler = Callable[
    [IncomingEvent, asyncpg.Record, asyncpg.Record, asyncpg.Record],
    Awaitable[OutgoingMessage | None],
]


_registry: dict[str, ScenarioHandler] = {}


def register_scenario(scenario_type: str) -> Callable[[ScenarioHandler], ScenarioHandler]:
    """Decorator: register a handler for a scenarios.type value.

    Example:
        @register_scenario('echo')
        async def handle_echo(event, user, conv, scenario):
            return OutgoingMessage(...)
    """
    def decorator(fn: ScenarioHandler) -> ScenarioHandler:
        if scenario_type in _registry:
            raise RuntimeError(
                f"Scenario type {scenario_type!r} already registered: "
                f"existing={_registry[scenario_type].__name__}, new={fn.__name__}"
            )
        _registry[scenario_type] = fn
        log.info("scenario_registered", type=scenario_type, handler=fn.__name__)
        return fn
    return decorator


def get_handler(scenario_type: str) -> ScenarioHandler | None:
    """Return registered handler for a type, or None."""
    return _registry.get(scenario_type)


def reset_registry() -> None:
    """Clear handler registry. Tests-only."""
    _registry.clear()


async def handle(
    event: IncomingEvent,
    user: asyncpg.Record,
    conversation: asyncpg.Record,
    is_new_user: bool,
) -> OutgoingMessage | None:
    """Route an event to the right scenario and return its OutgoingMessage (or None).

    Args:
        event: incoming event (message or comment)
        user: social_users row (just created or fetched)
        conversation: conversations row (active)
        is_new_user: True if `user` was created as part of processing this event
    """
    # Skip if conversation is in handover state — humans take over.
    if conversation["status"] in ("handover_pending", "handover_done"):
        log.info(
            "scenario_skipped_handover",
            user_id=user["id"],
            conv_status=conversation["status"],
        )
        return None

    # Determine keyword context
    context: KeywordContext = "comment" if event.event_type == "comment" else "dm"

    scenario_row: asyncpg.Record | None = None

    # 1. For comments: check post-specific triggers FIRST (post-local override).
    if event.event_type == "comment" and event.post_id and event.text:
        from app.repos import comment_triggers as ct_repo
        trigger = await ct_repo.find_for_post(event.platform, event.post_id, event.text)
        if trigger:
            scenario_row = await scenarios_repo.get_by_id(trigger["scenario_id"])
            if scenario_row is None:
                log.warning(
                    "comment_trigger_scenario_missing",
                    trigger_id=trigger["id"],
                    scenario_id=trigger["scenario_id"],
                )

    # 2. Fall back to global keywords match
    if scenario_row is None and event.text:
        km: KeywordMatch | None = await match_keywords(event.text, context)
        if km:
            scenario_row = await scenarios_repo.get_by_id(km.scenario_id)
            if scenario_row is None:
                log.warning(
                    "keyword_matched_but_scenario_missing",
                    keyword_id=km.keyword_id,
                    scenario_id=km.scenario_id,
                )

    # 3. New user, no keyword match → default welcome
    if scenario_row is None and is_new_user and event.event_type == "message":
        scenario_row = await scenarios_repo.get_default_welcome()

    # 4. Fallback: smart scenario (Claude-powered; was echo_scenario in early dev)
    if scenario_row is None:
        scenario_row = await scenarios_repo.get_by_name("default_smart")
        if scenario_row is None:
            log.warning("no_scenario_resolved_and_smart_missing")
            return None

    # Dispatch
    handler = get_handler(scenario_row["type"])
    if handler is None:
        log.warning(
            "no_handler_for_scenario_type",
            scenario_id=scenario_row["id"],
            scenario_type=scenario_row["type"],
        )
        return None

    log.info(
        "scenario_dispatch",
        scenario_id=scenario_row["id"],
        scenario_type=scenario_row["type"],
        scenario_name=scenario_row["name"],
        user_id=user["id"],
    )

    return await handler(event, user, conversation, scenario_row)
