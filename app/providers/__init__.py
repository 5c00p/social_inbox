"""Provider factory.

Returns a singleton MessagingProvider instance based on configuration.
Reads config once at first call; subsequent calls return the cached instance.

Usage in FastAPI dependencies:
    from app.providers import get_provider
    provider = get_provider()

In tests, override via monkeypatch on `_singleton` or by calling
reset_provider() (testing-only).
"""
from __future__ import annotations

from app.config import get_settings
from app.providers.base import MessagingProvider
from app.providers.manychat import ManychatProvider
from app.providers.meta import MetaProvider
from app.providers.sendpulse import SendPulseProvider

__all__ = [
    "MessagingProvider",
    "get_provider",
    "reset_provider",
]


_singleton: MessagingProvider | None = None


def get_provider() -> MessagingProvider:
    """Return the configured messaging provider singleton.

    Raises:
        ValueError if MESSAGING_PROVIDER is unknown.
        NotImplementedError if a placeholder provider is selected.
    """
    global _singleton
    if _singleton is not None:
        return _singleton

    settings = get_settings()
    provider_name = settings.messaging_provider

    if provider_name == "sendpulse":
        _singleton = SendPulseProvider(
            client_id=settings.sendpulse_client_id,
            client_secret=settings.sendpulse_client_secret,
            webhook_secret=settings.sendpulse_webhook_secret,
        )
    elif provider_name == "manychat":
        _singleton = ManychatProvider()
    elif provider_name == "meta":
        _singleton = MetaProvider()
    else:
        # Unreachable — pydantic-settings validates the literal — but defensive.
        raise ValueError(f"Unknown messaging provider: {provider_name!r}")

    return _singleton


def reset_provider() -> None:
    """Reset the cached singleton. Tests-only."""
    global _singleton
    _singleton = None
