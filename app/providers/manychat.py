"""Manychat messaging provider — placeholder.

Will be implemented if/when we migrate from SendPulse to Manychat
(e.g. when SendPulse pricing becomes prohibitive or features prove insufficient).

For now, this class exists only so MESSAGING_PROVIDER=manychat in .env
fails with a clear error rather than ImportError.
"""
from __future__ import annotations

from typing import Any, ClassVar

from app.models.enums import Platform, ProviderName
from app.models.events import IncomingEvent, OutgoingMessage
from app.providers.base import MessagingProvider


class ManychatProvider(MessagingProvider):
    """Placeholder for future Manychat integration."""

    name: ClassVar[ProviderName] = "manychat"

    def __init__(self) -> None:
        raise NotImplementedError(
            "ManychatProvider not yet implemented. "
            "If you need it, plan a dedicated task and update CLAUDE.md."
        )

    async def parse_webhook(
        self, raw_body: bytes, headers: dict[str, str],
    ) -> list[IncomingEvent]:
        raise NotImplementedError

    async def send(self, msg: OutgoingMessage) -> str | None:
        raise NotImplementedError

    async def fetch_user_profile(
        self, platform: Platform, external_user_id: str,
    ) -> dict[str, Any]:
        raise NotImplementedError
