"""Tests for admin notifications (mocked Telegram API)."""
from __future__ import annotations

import httpx
import pytest

from app.services import notifications


async def test_notify_admin_skips_when_no_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NOTIFICATION_BOT_TOKEN", "")
    monkeypatch.setenv("NOTIFICATION_ADMIN_CHAT_ID", "0")
    from app.config import get_settings
    get_settings.cache_clear()

    result = await notifications.notify_admin("test message")
    assert result is False


async def test_notify_admin_sends_when_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NOTIFICATION_BOT_TOKEN", "fake-token")
    monkeypatch.setenv("NOTIFICATION_ADMIN_CHAT_ID", "12345")
    from app.config import get_settings
    get_settings.cache_clear()

    captured: list[dict] = []

    class FakeResponse:
        def __init__(self) -> None:
            self.status_code = 200
            self.text = "ok"

    class FakeClient:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        async def __aenter__(self) -> FakeClient:
            return self

        async def __aexit__(self, *args: object) -> None:
            pass

        async def post(self, url: str, json: dict) -> FakeResponse:
            captured.append({"url": url, "json": json})
            return FakeResponse()

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)

    result = await notifications.notify_admin("Hello *Yulia*")

    assert result is True
    assert len(captured) == 1
    assert "fake-token" in captured[0]["url"]
    assert captured[0]["json"]["chat_id"] == 12345
    assert captured[0]["json"]["text"] == "Hello *Yulia*"
    assert captured[0]["json"]["parse_mode"] == "Markdown"


async def test_notify_admin_returns_false_on_http_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NOTIFICATION_BOT_TOKEN", "fake-token")
    monkeypatch.setenv("NOTIFICATION_ADMIN_CHAT_ID", "12345")
    from app.config import get_settings
    get_settings.cache_clear()

    class FakeClient:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        async def __aenter__(self) -> FakeClient:
            return self

        async def __aexit__(self, *args: object) -> None:
            pass

        async def post(self, *args: object, **kwargs: object) -> None:
            raise httpx.ConnectError("network down")

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)

    result = await notifications.notify_admin("test")
    assert result is False
