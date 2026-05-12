"""Webhook endpoints — entry point for messaging providers.

Pattern:
1. Read raw body (DO NOT parse before signature check).
2. Pass to provider.parse_webhook() for validation + parsing.
3. Log raw payload to events_log.
4. Enqueue each parsed event for the worker.
5. Always return 200 OK, even on parse failures —
   otherwise the provider may mark our endpoint as broken.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from app.observability.alerts import fire_alert
from app.providers import MessagingProvider, get_provider
from app.repos import events as events_repo
from app.repos.redis_client import get_redis
from app.utils.logging import get_logger
from app.workers.enqueue import enqueue_event

log = get_logger(__name__)

router = APIRouter(tags=["webhooks"])


def _provider_dep() -> MessagingProvider:
    """FastAPI dependency wrapper around the singleton factory.

    Tests override via app.dependency_overrides[_provider_dep] = lambda: FakeProvider().
    """
    return get_provider()


@router.get("/webhooks/{provider_name}")
async def webhook_verification(provider_name: str, request: Request) -> dict[str, str]:
    """Some providers send a verification GET request when subscribing.

    For SendPulse this is unused, but Meta-style providers send a hub.challenge.
    We respond generically with 'ok' for now; provider-specific handling can be
    added when MetaProvider is implemented.
    """
    log.info("webhook_verification_received", provider=provider_name)
    challenge = request.query_params.get("hub.challenge")
    if challenge:
        return {"hub.challenge": challenge}
    return {"status": "ok"}


@router.post("/webhooks/{provider_name}")
async def webhook_receive(
    provider_name: str,
    request: Request,
    provider: MessagingProvider = Depends(_provider_dep),
) -> dict[str, str]:
    """Receive a webhook from a messaging provider.

    Always returns 200 OK to keep the provider from marking us broken.
    """
    raw_body = await request.body()
    headers = dict(request.headers)

    if provider_name != provider.name:
        log.warning(
            "webhook_provider_mismatch",
            url_provider=provider_name,
            active_provider=provider.name,
        )

    try:
        events = await provider.parse_webhook(raw_body, headers)
    except Exception as exc:
        log.exception("webhook_parse_failed", provider=provider_name, error=str(exc))
        await _record_parse_failure()
        events = []

    log.info(
        "webhook_received",
        provider=provider_name,
        events_count=len(events),
        body_size=len(raw_body),
    )

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
            log.warning(
                "webhook_event_persist_failed",
                external_event_id=event.external_event_id,
                error=str(exc),
            )

    return {"status": "ok"}


async def _record_parse_failure() -> None:
    """Increment parse failure counter; alert if threshold breached."""
    redis = await get_redis()
    key = "webhook:parse_failures"
    count = await redis.incr(key)
    if count == 1:
        await redis.expire(key, 3600)  # 1-hour window

    if count >= 10:
        await fire_alert(
            "webhook_parse_failures",
            f"Получено {count} ошибок парсинга webhook за последний час. "
            f"Возможны проблемы у провайдера или изменения в их API.",
        )
