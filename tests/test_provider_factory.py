"""Tests for app.providers.get_provider() factory."""
from __future__ import annotations

import pytest

from app.config import get_settings
from app.providers import get_provider, reset_provider
from app.providers.sendpulse import SendPulseProvider


@pytest.fixture(autouse=True)
def _reset() -> None:
    """Reset singleton before each test."""
    reset_provider()
    yield
    reset_provider()


def test_factory_returns_sendpulse_by_default() -> None:
    """Default config (set in conftest.py) is sendpulse."""
    provider = get_provider()
    assert isinstance(provider, SendPulseProvider)
    assert provider.name == "sendpulse"


def test_factory_returns_singleton() -> None:
    p1 = get_provider()
    p2 = get_provider()
    assert p1 is p2


def test_factory_manychat_raises_not_implemented(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MESSAGING_PROVIDER", "manychat")
    get_settings.cache_clear()
    reset_provider()
    with pytest.raises(NotImplementedError, match="ManychatProvider"):
        get_provider()


def test_factory_meta_raises_not_implemented(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MESSAGING_PROVIDER", "meta")
    get_settings.cache_clear()
    reset_provider()
    with pytest.raises(NotImplementedError, match="MetaProvider"):
        get_provider()
