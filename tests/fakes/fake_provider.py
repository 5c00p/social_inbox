"""In-memory fake provider for tests.

Use as a drop-in replacement for SendPulseProvider in tests of
webhook handlers, ScenarioEngine, and end-to-end flows.

Captures all sent messages in `sent` for assertions.
Lets tests control what parse_webhook returns via `queued_events`.
"""
from __future__ import annotations

from typing import Any, ClassVar

from app.models.enums import Platform, ProviderName
from app.models.events import IncomingEvent, OutgoingMessage
from app.providers.base import MessagingProvider


class FakeProvider(MessagingProvider):
    """Test double for MessagingProvider."""

    name: ClassVar[ProviderName] = "sendpulse"  # mimic real provider name

    def __init__(self) -> None:
        self.queued_events: list[IncomingEvent] = []
        self.sent: list[OutgoingMessage] = []
        self.profiles: dict[tuple[Platform, str], dict[str, Any]] = {}
        self.send_should_fail: bool = False
        self.signature_valid: bool = True

    async def parse_webhook(
        self,
        raw_body: bytes,
        headers: dict[str, str],
    ) -> list[IncomingEvent]:
        if not self.signature_valid:
            return []
        events = list(self.queued_events)
        self.queued_events.clear()
        return events

    async def send(self, msg: OutgoingMessage) -> str | None:
        if self.send_should_fail:
            return None
        self.sent.append(msg)
        return f"fake_msg_id_{len(self.sent)}"

    async def fetch_user_profile(
        self,
        platform: Platform,
        external_user_id: str,
    ) -> dict[str, Any]:
        return self.profiles.get((platform, external_user_id), {})

    # --- helpers for tests ---

    def queue_event(self, event: IncomingEvent) -> None:
        """Queue an event to be returned on next parse_webhook call."""
        self.queued_events.append(event)

    def set_profile(
        self,
        platform: Platform,
        external_user_id: str,
        profile: dict[str, Any],
    ) -> None:
        """Pre-populate a profile for fetch_user_profile to return."""
        self.profiles[(platform, external_user_id)] = profile

    def reset(self) -> None:
        """Clear all internal state. Call between tests."""
        self.queued_events.clear()
        self.sent.clear()
        self.profiles.clear()
        self.send_should_fail = False
        self.signature_valid = True
