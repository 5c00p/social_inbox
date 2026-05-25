"""Lead context endpoint — entry point for bot_purify.

Endpoints:
- GET  /api/lead/{short_id}            — fetch lead context
- POST /api/lead/{short_id}/handover   — record successful Telegram handover

Auth: X-Internal-Token (shared secret with bot_purify).
Rate limit: 60 RPM per token fingerprint.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, status

from app.api.auth import fingerprint_token, verify_internal_token
from app.models.lead import (
    HandoverRequest,
    HandoverResponse,
    LeadMessage,
    LeadResponse,
    LeadUserInfo,
)
from app.repos import messages as messages_repo
from app.repos import users as users_repo
from app.services import lead_tracker
from app.services.rate_limiter import can_call_internal_api
from app.utils.logging import get_logger

log = get_logger(__name__)

router = APIRouter(prefix="/api/lead", tags=["lead"])


async def _check_rate_limit(
    x_internal_token: str | None = Header(default=None, alias="X-Internal-Token"),
) -> None:
    """Rate-limit dependency. Runs after verify_internal_token.

    By the time we reach here, token is non-empty and valid.
    """
    if not x_internal_token:
        return  # verify_internal_token already raised; defensive
    fp = fingerprint_token(x_internal_token)
    allowed = await can_call_internal_api(fp)
    if not allowed:
        log.warning("internal_api_rate_limited", fingerprint=fp)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Rate limit exceeded — 60 requests per minute",
        )


@router.get(
    "/{short_id}",
    response_model=LeadResponse,
    dependencies=[Depends(verify_internal_token), Depends(_check_rate_limit)],
)
async def get_lead(short_id: str) -> LeadResponse:
    """Return lead context for bot_purify.

    Returns 404 if short_id is not found or the user was soft-deleted.
    Returns 401 if X-Internal-Token is missing or invalid.
    Returns 429 if rate limit exceeded.
    """
    user = await users_repo.get_by_short_id(short_id)
    if user is None:
        log.info("lead_not_found", short_id=short_id)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Lead not found",
        )

    scenario_slug = await _resolve_scenario_slug(user["id"])

    msg_rows = await messages_repo.get_recent_for_user(user["id"], limit=10)
    recent = [
        LeadMessage(
            direction=row["direction"],
            text=row["text"],
            created_at=row["created_at"],
        )
        for row in msg_rows
    ]

    response = LeadResponse(
        user=LeadUserInfo(
            platform=user["platform"],
            username=user["username"],
            full_name=user["full_name"],
            first_seen_at=user["first_seen_at"],
        ),
        scenario=scenario_slug,
        recent_messages=recent,
    )

    log.info(
        "lead_fetched",
        short_id=short_id,
        user_id=user["id"],
        message_count=len(recent),
        scenario=scenario_slug,
    )

    return response


@router.post(
    "/{short_id}/handover",
    response_model=HandoverResponse,
    dependencies=[Depends(verify_internal_token), Depends(_check_rate_limit)],
)
async def post_handover(short_id: str, body: HandoverRequest) -> HandoverResponse:
    """Record that the lead has successfully landed in bot_purify.

    Sets social_users.tg_handover_at and social_users.tg_user_id.
    Idempotent: calling again with same tg_user_id is a no-op.
    Returns 404 if short_id not found.
    """
    user = await users_repo.get_by_short_id(short_id)
    if user is None:
        log.info("handover_lead_not_found", short_id=short_id)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Lead not found",
        )

    await lead_tracker.record_handover(
        user_id=user["id"],
        tg_user_id=body.tg_user_id,
    )

    updated = await users_repo.get_by_short_id(short_id)
    assert updated is not None  # we just updated this record
    handed_over_at = updated["tg_handover_at"]

    log.info(
        "handover_recorded",
        short_id=short_id,
        user_id=user["id"],
        tg_user_id=body.tg_user_id,
    )

    return HandoverResponse(
        status="ok",
        tg_user_id=body.tg_user_id,
        handed_over_at=handed_over_at,
    )


async def _resolve_scenario_slug(user_id: int) -> str:
    """Extract scenario_slug from the most recent outgoing message's scenario metadata.

    Falls back to 'unknown' if:
    - No outgoing messages exist yet (user just sent first DM, no reply yet)
    - Last out-message has no scenario_id (e.g. echo fallback in early dev)
    - Scenario metadata doesn't contain tg_scenario_slug
    """
    row = await users_repo.get_last_outgoing_with_scenario(user_id)
    if row is None:
        return "unknown"
    metadata = row["scenario_metadata"]
    if not metadata:
        return "unknown"
    if isinstance(metadata, dict):
        return str(metadata.get("tg_scenario_slug", "unknown"))
    return "unknown"
