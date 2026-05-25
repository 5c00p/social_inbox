"""Pydantic models for /api/lead/{short_id} endpoint.

These models are the **public contract** with bot_purify.
Any change requires coordinated update in bot_purify/services/social_inbox.py.

Versioning policy: this is a v1 contract. Breaking changes require new path
(/api/v2/lead/...) and parallel deployment with deprecation period.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import Direction, Platform


class LeadUserInfo(BaseModel):
    """Subset of social_users data exposed to bot_purify."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    platform: Platform
    username: str | None = None
    full_name: str | None = None
    first_seen_at: datetime


class LeadMessage(BaseModel):
    """A single message in lead's conversation history.

    Only direction + text + timestamp — no internal metadata
    (claude_tokens, raw_payload, scenario_id, etc.) to keep contract minimal.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    direction: Direction
    text: str | None = None
    created_at: datetime


class LeadResponse(BaseModel):
    """Response body for GET /api/lead/{short_id}.

    Contract documented in CLAUDE.md § 9.2.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    user: LeadUserInfo
    scenario: str = Field(
        description=(
            "scenario_slug from the deep-link that brought the user "
            "(e.g. 'purify', 'oils', 'faq'). Falls back to 'unknown' "
            "if no outgoing message with a slug exists yet."
        )
    )
    recent_messages: list[LeadMessage]


class HandoverRequest(BaseModel):
    """Body for POST /api/lead/{short_id}/handover."""

    model_config = ConfigDict(extra="forbid")

    tg_user_id: int = Field(gt=0, description="Telegram user ID that landed in bot_purify")


class HandoverResponse(BaseModel):
    """Response for POST /api/lead/{short_id}/handover."""

    model_config = ConfigDict(extra="forbid")

    status: str
    tg_user_id: int
    handed_over_at: datetime
