"""Shared enum-like literals used across the project."""

from __future__ import annotations

from typing import Literal

Platform = Literal["instagram", "facebook"]
Direction = Literal["in", "out"]
ConversationStatus = Literal[
    "active",
    "closed",
    "handover_pending",
    "handover_done",
]
ScenarioType = Literal[
    "welcome",
    "comment_to_dm",
    "faq",
    "handover",
    "smart",
]
EventType = Literal["message", "comment", "postback"]
ProviderName = Literal["sendpulse", "manychat", "meta"]
