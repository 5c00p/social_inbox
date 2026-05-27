"""Tests for SendPulseProvider — parsing real /chats response items."""

from __future__ import annotations

import json as _json
import re
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
from pytest_httpx import HTTPXMock

from app.models.events import OutgoingMessage, QuickReply
from app.providers.sendpulse import SendPulseProvider
from app.providers.sendpulse_cursor import set_cursor
from app.repos.redis_client import get_redis

_CHATS_RE = re.compile(r"^https://api\.sendpulse\.com/instagram/chats")


# ─── Fixtures: real shapes from Yulia's account ───

CHAT_INCOMING_TEXT: dict = {
    "inbox_last_message": {
        "contact_id": "6a14d242a0cb77b3d00abf18",
        "bot_id": "6a0b5c1e35f395f025034fb6",
        "type": "text",
        "direction": 1,
        "created_at": "2026-05-25T22:50:43+00:00",
        "id": "6a14d243bea27c81cb0a7492",
        "data": {
            "text": "Здравствуйте! Какие масла посоветуете от гриппа и простуды?",
            "is_echo": False,
            "is_deleted": False,
        },
    },
    "inbox_unread": 1,
    "contact": {
        "id": "6a14d242a0cb77b3d00abf18",
        "bot_id": "6a0b5c1e35f395f025034fb6",
        "channel_data": {
            "id": 4280767578903322,
            "name": "Svetlana",
            "user_name": "svetlana_parf_orig",
            "first_name": "Svetlana",
        },
        "last_activity_at": "2026-05-25T22:50:43+00:00",
    },
}

CHAT_REPLY_TO_STORY: dict = {
    "inbox_last_message": {
        "contact_id": "6a0c09208be53535bc0e5514",
        "type": "reply_to_story",
        "direction": 1,
        "created_at": "2026-05-26T07:12:39+00:00",
        "id": "6a1547e7e69bccac5600817c",
        "data": {
            "text": "🔥",
            "is_echo": False,
            "reply_to": {"story": "story:18..."},
        },
    },
    "inbox_unread": 32,
    "contact": {
        "id": "6a0c09208be53535bc0e5514",
        "channel_data": {
            "name": "анна прощенко",
            "user_name": "proshchenko105",
        },
    },
}

CHAT_OUTGOING: dict = {
    "inbox_last_message": {
        "contact_id": "u1",
        "type": "text",
        "direction": 2,  # outgoing — skip
        "created_at": "2026-05-26T08:00:00+00:00",
        "id": "msg_outgoing",
        "data": {"text": "Our reply", "is_echo": False},
    },
    "contact": {"id": "u1", "channel_data": {"user_name": "x"}},
}

CHAT_ECHO: dict = {
    "inbox_last_message": {
        "contact_id": "u2",
        "type": "text",
        "direction": 1,
        "created_at": "2026-05-26T08:00:00+00:00",
        "id": "msg_echo",
        "data": {"text": "echo", "is_echo": True},
    },
    "contact": {"id": "u2", "channel_data": {"user_name": "x"}},
}

CHAT_UNSUPPORTED_TYPE: dict = {
    "inbox_last_message": {
        "contact_id": "u3",
        "type": "image",
        "direction": 1,
        "created_at": "2026-05-26T08:00:00+00:00",
        "id": "msg_img",
        "data": {"text": None, "is_echo": False},
    },
    "contact": {"id": "u3", "channel_data": {"user_name": "x"}},
}


@pytest.fixture(autouse=True)
async def _clear_keys() -> AsyncIterator[None]:
    redis = await get_redis()
    keys = await redis.keys("sendpulse:*")
    if keys:
        await redis.delete(*keys)
    yield
    keys = await redis.keys("sendpulse:*")
    if keys:
        await redis.delete(*keys)


def _make_provider() -> SendPulseProvider:
    return SendPulseProvider("cid", "csecret", webhook_secret="wsecret")


# ────────── Parsing ──────────


def test_parse_chat_text_message() -> None:
    p = _make_provider()
    event = p._parse_chat_item(CHAT_INCOMING_TEXT)
    assert event is not None
    assert event.external_user_id == "6a14d242a0cb77b3d00abf18"
    assert event.external_event_id == "6a14d243bea27c81cb0a7492"
    assert event.username == "svetlana_parf_orig"
    assert event.full_name == "Svetlana"
    assert event.text is not None
    assert "грипп" in event.text
    assert event.event_type == "message"


def test_parse_chat_reply_to_story_as_text() -> None:
    p = _make_provider()
    event = p._parse_chat_item(CHAT_REPLY_TO_STORY)
    assert event is not None
    assert event.text == "🔥"
    assert event.username == "proshchenko105"


def test_parse_skips_outgoing() -> None:
    p = _make_provider()
    assert p._parse_chat_item(CHAT_OUTGOING) is None


def test_parse_skips_echo() -> None:
    p = _make_provider()
    assert p._parse_chat_item(CHAT_ECHO) is None


def test_parse_skips_unsupported_type() -> None:
    p = _make_provider()
    assert p._parse_chat_item(CHAT_UNSUPPORTED_TYPE) is None


def test_parse_handles_missing_inbox_last_message() -> None:
    p = _make_provider()
    assert p._parse_chat_item({"contact": {}}) is None


def test_parse_handles_missing_contact_id() -> None:
    p = _make_provider()
    broken: dict = {
        "inbox_last_message": {
            "type": "text",
            "direction": 1,
            "id": "x",
            "created_at": "2026-05-26T08:00:00+00:00",
            "data": {"text": "hi", "is_echo": False},
        },
        "contact": {},
    }
    assert p._parse_chat_item(broken) is None


# ────────── Polling ──────────


async def test_polling_returns_new_events(
    httpx_mock: HTTPXMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SENDPULSE_BOT_ID", "bot_test")
    from app.config import get_settings

    get_settings.cache_clear()

    await set_cursor("bot_test", datetime(2026, 5, 25, 0, 0, tzinfo=UTC))

    httpx_mock.add_response(
        method="POST",
        url="https://api.sendpulse.com/oauth/access_token",
        json={"access_token": "tok"},
    )
    httpx_mock.add_response(
        method="GET",
        url=_CHATS_RE,
        json={
            "success": True,
            "data": [
                CHAT_INCOMING_TEXT,
                CHAT_REPLY_TO_STORY,
                CHAT_OUTGOING,
                CHAT_ECHO,
                CHAT_UNSUPPORTED_TYPE,
            ],
            "meta": {"total": 5, "limit": 50},
        },
    )

    p = _make_provider()
    events = await p.poll_new_events()

    assert len(events) == 2
    user_ids = {e.external_user_id for e in events}
    assert user_ids == {
        "6a14d242a0cb77b3d00abf18",
        "6a0c09208be53535bc0e5514",
    }


async def test_polling_advances_cursor(
    httpx_mock: HTTPXMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SENDPULSE_BOT_ID", "bot_test")
    from app.config import get_settings

    get_settings.cache_clear()

    await set_cursor("bot_test", datetime(2026, 5, 25, 0, 0, tzinfo=UTC))

    httpx_mock.add_response(
        method="POST",
        url="https://api.sendpulse.com/oauth/access_token",
        json={"access_token": "tok"},
    )
    httpx_mock.add_response(
        method="GET",
        url=_CHATS_RE,
        json={"success": True, "data": [CHAT_INCOMING_TEXT]},
    )
    p = _make_provider()
    await p.poll_new_events()

    from app.providers.sendpulse_cursor import get_cursor

    new_cursor = await get_cursor("bot_test")
    assert new_cursor == datetime(2026, 5, 25, 22, 50, 43, tzinfo=UTC)


async def test_polling_skips_old_messages(
    httpx_mock: HTTPXMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SENDPULSE_BOT_ID", "bot_test")
    from app.config import get_settings

    get_settings.cache_clear()

    await set_cursor("bot_test", datetime(2026, 12, 31, tzinfo=UTC))

    httpx_mock.add_response(
        method="POST",
        url="https://api.sendpulse.com/oauth/access_token",
        json={"access_token": "tok"},
    )
    httpx_mock.add_response(
        method="GET",
        url=_CHATS_RE,
        json={"success": True, "data": [CHAT_INCOMING_TEXT]},
    )
    p = _make_provider()
    events = await p.poll_new_events()
    assert events == []


async def test_polling_follows_pagination(
    httpx_mock: HTTPXMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SENDPULSE_BOT_ID", "bot_test")
    from app.config import get_settings

    get_settings.cache_clear()
    await set_cursor("bot_test", datetime(2026, 5, 1, tzinfo=UTC))

    httpx_mock.add_response(
        method="POST",
        url="https://api.sendpulse.com/oauth/access_token",
        json={"access_token": "tok"},
    )
    next_url = "https://api.sendpulse.com/api/instagram/chats?page=2&jwt=x"
    httpx_mock.add_response(
        method="GET",
        url=_CHATS_RE,
        json={
            "success": True,
            "data": [CHAT_INCOMING_TEXT],
            "links": {"next": next_url},
        },
    )
    httpx_mock.add_response(
        method="GET",
        url=next_url,
        json={"success": True, "data": [CHAT_REPLY_TO_STORY]},
    )

    p = _make_provider()
    events = await p.poll_new_events()
    assert len(events) == 2


# ────────── Send ──────────


async def test_send_text(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        method="POST",
        url="https://api.sendpulse.com/oauth/access_token",
        json={"access_token": "tok"},
    )
    httpx_mock.add_response(
        method="POST",
        url="https://api.sendpulse.com/instagram/contacts/send",
        json={"success": True, "data": {"id": "out_msg_1"}},
    )
    p = _make_provider()
    result = await p.send(
        OutgoingMessage(
            platform="instagram",
            external_user_id="contact_x",
            text="Привет!",
        )
    )
    assert result == "out_msg_1"


async def test_send_with_url_button(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        method="POST",
        url="https://api.sendpulse.com/oauth/access_token",
        json={"access_token": "tok"},
    )
    httpx_mock.add_response(
        method="POST",
        url="https://api.sendpulse.com/instagram/contacts/send",
        json={"success": True, "data": {"id": "out_btn_1"}},
    )
    p = _make_provider()
    await p.send(
        OutgoingMessage(
            platform="instagram",
            external_user_id="contact_x",
            text="Перейди в Telegram",
            quick_replies=[
                QuickReply(
                    title="Перейти",
                    payload="https://t.me/yuliya_purify_bot?start=ig_abc12345_purify",
                )
            ],
        )
    )

    sent = httpx_mock.get_requests(
        url="https://api.sendpulse.com/instagram/contacts/send",
    )[0]
    body = _json.loads(sent.content)
    block = body["messages"][0]
    assert block["type"] == "generic_template"
    elements = block["message"]["attachment"]["payload"]["elements"]
    assert elements[0]["buttons"][0]["type"] == "web_url"
    assert elements[0]["buttons"][0]["url"].startswith("https://t.me/")


async def test_send_returns_none_on_error(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        method="POST",
        url="https://api.sendpulse.com/oauth/access_token",
        json={"access_token": "tok"},
    )
    for _ in range(4):
        httpx_mock.add_response(
            method="POST",
            url="https://api.sendpulse.com/instagram/contacts/send",
            status_code=500,
        )
    p = _make_provider()
    result = await p.send(
        OutgoingMessage(
            platform="instagram",
            external_user_id="contact_x",
            text="x",
        )
    )
    assert result is None
