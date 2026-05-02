"""SendPulse messaging provider — skeleton.

Full implementation of API methods is in Task 05.
This file establishes the class structure so that get_provider() can
return an instance and the rest of the wiring works.
"""
from __future__ import annotations

from typing import Any, ClassVar

from app.models.enums import Platform, ProviderName
from app.models.events import IncomingEvent, OutgoingMessage
from app.providers.base import MessagingProvider
from app.utils.logging import get_logger

log = get_logger(__name__)


class SendPulseProvider(MessagingProvider):
    """Implementation of MessagingProvider for SendPulse.

    Documentation: https://sendpulse.com/integrations/api/chatbot/instagram

    Auth: OAuth2 client_credentials (CLIENT_ID + CLIENT_SECRET → bearer token).
    Token cached in Redis with TTL of 50 minutes (provider issues 60-min tokens).

    Webhook signature: SendPulse signs webhooks via HMAC-SHA256 over the body
    using the chatbot's secret. Header: `X-Signature` (verify in parse_webhook).
    """

    name: ClassVar[ProviderName] = "sendpulse"

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        webhook_secret: str,
    ) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._webhook_secret = webhook_secret

    async def parse_webhook(
        self,
        raw_body: bytes,
        headers: dict[str, str],
    ) -> list[IncomingEvent]:
        # Implemented in Task 05
        raise NotImplementedError("SendPulseProvider.parse_webhook — Task 05")

    async def send(self, msg: OutgoingMessage) -> str | None:
        # Implemented in Task 05
        raise NotImplementedError("SendPulseProvider.send — Task 05")

    async def fetch_user_profile(
        self,
        platform: Platform,
        external_user_id: str,
    ) -> dict[str, Any]:
        # Implemented in Task 05
        raise NotImplementedError("SendPulseProvider.fetch_user_profile — Task 05")
