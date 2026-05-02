"""Pydantic models for events flowing through the system.

These models are the **lingua franca** between MessagingProvider implementations
and the ScenarioEngine. Every provider must produce IncomingEvent and accept
OutgoingMessage. The internal logic does not depend on which provider is active.

Why Pydantic, not @dataclass:
- These models are JSON-serialized into the arq queue
- We want runtime validation of platform / direction / event_type literals
- Pydantic auto-generates JSON schema for OpenAPI docs in admin endpoints
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import EventType, Platform, ProviderName


class IncomingEvent(BaseModel):
    """A normalized event received from any messaging platform.

    Producers: MessagingProvider implementations (parse_webhook).
    Consumers: ScenarioEngine, message workers.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    provider: ProviderName
    platform: Platform
    event_type: EventType
    external_user_id: str = Field(min_length=1, max_length=255)
    external_event_id: str = Field(min_length=1, max_length=255)
    username: str | None = None
    full_name: str | None = None
    text: str | None = None
    media_url: str | None = None
    post_id: str | None = None         # set when event_type='comment'
    comment_id: str | None = None      # set when event_type='comment'
    occurred_at: datetime
    raw_payload: dict[str, Any] = Field(default_factory=dict)


class QuickReply(BaseModel):
    """A button that appears under a message and submits a payload when tapped."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    title: str = Field(min_length=1, max_length=20)   # platform display limits
    payload: str = Field(min_length=1, max_length=1000)


class OutgoingMessage(BaseModel):
    """A message to be sent via a MessagingProvider.

    Producers: ScenarioEngine.
    Consumers: MessagingProvider implementations (send).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    platform: Platform
    external_user_id: str = Field(min_length=1, max_length=255)
    text: str | None = None
    quick_replies: list[QuickReply] | None = None
    media_url: str | None = None
    reply_to_comment_id: str | None = None  # if set, this is a comment private-reply
    scenario_id: int | None = None          # for tracking, optional
