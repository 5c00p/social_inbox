"""Meta messaging provider — placeholder.

Will be implemented when we have a verified business entity and a Meta App
with approved permissions (instagram_business_manage_messages, etc.).
See docs/meta_app_review_guide.md for the prerequisites.

Direct integration with Meta Graph API gives us full control and removes
the SendPulse middleman, but requires significant compliance setup.
"""
from __future__ import annotations

from typing import Any, ClassVar

from app.models.enums import Platform, ProviderName
from app.models.events import IncomingEvent, OutgoingMessage
from app.providers.base import MessagingProvider


class MetaProvider(MessagingProvider):
    """Placeholder for future direct Meta Graph API integration."""

    name: ClassVar[ProviderName] = "meta"

    def __init__(self) -> None:
        raise NotImplementedError(
            "MetaProvider requires a verified business entity in Meta App Review. "
            "See docs/meta_app_review_guide.md before enabling."
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
