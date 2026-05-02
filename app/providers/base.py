"""Abstract base class for all messaging-platform integrations.

Each platform integration (SendPulse, Manychat, Meta) implements this interface.
The rest of the application talks ONLY through this abstraction —
no direct calls to platform-specific SDKs in scenario_engine, claude_responder, etc.

Lifecycle:
1. Webhook arrives → endpoint passes raw bytes + headers to provider.parse_webhook()
2. Provider validates signature, parses JSON, returns list[IncomingEvent]
3. Endpoint enqueues each IncomingEvent into arq queue
4. Worker pulls from queue → ScenarioEngine processes → produces OutgoingMessage
5. Worker calls provider.send(OutgoingMessage)
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, ClassVar

from app.models.enums import Platform, ProviderName
from app.models.events import IncomingEvent, OutgoingMessage


class MessagingProvider(ABC):
    """Interface for messaging platform integration."""

    name: ClassVar[ProviderName]  # subclasses MUST override with a literal value

    @abstractmethod
    async def parse_webhook(
        self,
        raw_body: bytes,
        headers: dict[str, str],
    ) -> list[IncomingEvent]:
        """Parse and normalize an incoming webhook payload.

        Contract:
        - Returns empty list if signature is invalid (does NOT raise).
          The caller (webhook endpoint) will log and return 200 OK regardless.
        - Returns empty list for non-event pings (e.g. health check from provider).
        - Returns one or more IncomingEvent for actual events.
        - MUST be idempotent: calling twice with same payload → same result.

        Why no raise on invalid signature: webhook endpoint must always return 200,
        otherwise the provider may mark our endpoint as broken and stop sending events.
        """
        ...

    @abstractmethod
    async def send(self, msg: OutgoingMessage) -> str | None:
        """Send a message via the platform's API.

        Returns:
        - external_message_id (str) on success — the platform's ID for this message,
          stored in messages.external_message_id for idempotency.
        - None on failure. Caller may retry via arq's retry mechanism.

        Failures should be logged inside the implementation, not raised.
        Exceptions are reserved for unrecoverable bugs (programmer error).
        """
        ...

    @abstractmethod
    async def fetch_user_profile(
        self,
        platform: Platform,
        external_user_id: str,
    ) -> dict[str, Any]:
        """Fetch a user's public profile (username, full_name, avatar URL).

        Returns a dict with optional keys: 'username', 'full_name', 'profile_pic_url'.
        Returns empty dict {} if the platform doesn't expose this info or call fails.

        This is OPTIONAL — many flows don't need profile lookup.
        ScenarioEngine should not depend on the result being non-empty.
        """
        ...
