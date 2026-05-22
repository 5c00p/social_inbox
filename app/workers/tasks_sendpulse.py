"""arq task: poll SendPulse for new events.

Runs every N seconds (configured by SENDPULSE_POLLING_INTERVAL_SECONDS via cron
schedule in arq_settings.py). Enqueues IncomingEvents into the same queue used
by webhook handler, so downstream worker (process_incoming_event) is
provider-agnostic.

Skipped if SENDPULSE_POLLING_ENABLED=false (paid plan with webhooks) or if
MESSAGING_PROVIDER != "sendpulse".
"""

from __future__ import annotations

from typing import Any

from app.config import get_settings
from app.providers import get_provider
from app.providers.sendpulse import SendPulseProvider
from app.repos import events as events_repo
from app.utils.logging import get_logger
from app.workers.enqueue import enqueue_event

log = get_logger(__name__)


async def sendpulse_poll_tick(ctx: dict[str, Any]) -> None:
    """One polling iteration. Idempotent — safe to run more often than needed."""
    settings = get_settings()
    if not settings.sendpulse_polling_enabled:
        return
    if settings.messaging_provider != "sendpulse":
        return

    provider = get_provider()
    if not isinstance(provider, SendPulseProvider):
        log.warning(
            "sendpulse_poller_provider_mismatch",
            actual=type(provider).__name__,
        )
        return

    try:
        events = await provider.poll_new_events()
    except Exception as exc:
        log.exception("sendpulse_poll_failed", error=str(exc))
        return

    if not events:
        return

    log.info("sendpulse_poll_events_enqueueing", count=len(events))
    for event in events:
        try:
            row = await events_repo.insert(
                provider_name=event.provider,
                platform=event.platform,
                event_type=event.event_type,
                external_event_id=event.external_event_id,
                payload=event.raw_payload,
                signature_valid=True,
            )
            await enqueue_event(event, row["id"])
        except Exception as exc:
            # Most likely UniqueViolationError on external_event_id — dedup
            log.debug(
                "sendpulse_poll_event_dedup_or_error",
                external_event_id=event.external_event_id,
                error=str(exc)[:100],
            )
