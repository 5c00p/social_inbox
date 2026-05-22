"""Tests for SendPulseProvider — parsing, polling, sending."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import re
from collections.abc import AsyncIterator

import pytest
from pytest_httpx import HTTPXMock

from app.models.events import OutgoingMessage, QuickReply
from app.providers.sendpulse import SendPulseProvider
from app.repos.redis_client import get_redis

# pytest-httpx 0.35 takes a compiled regex for prefix-style matching.
_MESSAGES_RE = re.compile(r"^https://api\.sendpulse\.com/instagram/messages")
_COMMENTS_RE = re.compile(r"^https://api\.sendpulse\.com/instagram/comments")


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
    return SendPulseProvider(
        client_id="test_cid",
        client_secret="test_csecret",
        webhook_secret="test_wsecret",
    )


# ---- parse_message_item ----


def test_parse_message_item_basic() -> None:
    provider = _make_provider()
    raw = {
        "id": "msg_123",
        "contact": {"id": "user_456", "username": "anna", "name": "Anna P"},
        "text": "Привет",
        "created_at": "2026-05-15T12:00:00Z",
    }
    event = provider._parse_message_item(raw)
    assert event is not None
    assert event.external_user_id == "user_456"
    assert event.external_event_id == "msg_123"
    assert event.username == "anna"
    assert event.full_name == "Anna P"
    assert event.text == "Привет"
    assert event.event_type == "message"
    assert event.platform == "instagram"


def test_parse_message_item_missing_user_id_returns_none() -> None:
    provider = _make_provider()
    raw = {"id": "msg_X", "text": "hi"}
    assert provider._parse_message_item(raw) is None


def test_parse_comment_item_basic() -> None:
    provider = _make_provider()
    raw = {
        "id": "cmt_999",
        "from": {"id": "user_777", "username": "bob"},
        "text": "ОЧИЩЕНИЕ",
        "post_id": "post_42",
        "created_at": 1715774400,
    }
    event = provider._parse_comment_item(raw)
    assert event is not None
    assert event.event_type == "comment"
    assert event.external_user_id == "user_777"
    assert event.post_id == "post_42"
    assert event.comment_id == "cmt_999"
    assert event.text == "ОЧИЩЕНИЕ"


# ---- send ----


async def test_send_dm_text(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        method="POST",
        url="https://api.sendpulse.com/oauth/access_token",
        json={"access_token": "tok"},
    )
    httpx_mock.add_response(
        method="POST",
        url="https://api.sendpulse.com/instagram/contacts/send",
        json={"success": True, "data": [{"id": "sent_msg_xyz"}]},
    )

    provider = _make_provider()
    msg = OutgoingMessage(
        platform="instagram",
        external_user_id="user_1",
        text="Привет!",
    )
    result = await provider.send(msg)
    assert result == "sent_msg_xyz"

    requests = httpx_mock.get_requests(url="https://api.sendpulse.com/instagram/contacts/send")
    assert len(requests) == 1
    body = json.loads(requests[0].content)
    assert body["contact_id"] == "user_1"
    assert body["messages"][0]["message"]["text"] == "Привет!"


async def test_send_dm_with_quick_replies(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        method="POST",
        url="https://api.sendpulse.com/oauth/access_token",
        json={"access_token": "tok"},
    )
    httpx_mock.add_response(
        method="POST",
        url="https://api.sendpulse.com/instagram/contacts/send",
        json={"data": [{"id": "qr_msg"}]},
    )
    provider = _make_provider()
    msg = OutgoingMessage(
        platform="instagram",
        external_user_id="user_1",
        text="Выбери:",
        quick_replies=[
            QuickReply(title="Перейти в TG", payload="https://t.me/yuliya_purify_bot?start=ig_xxx"),
            QuickReply(title="Узнать больше", payload="more_info"),
        ],
    )
    result = await provider.send(msg)
    assert result == "qr_msg"

    sent = httpx_mock.get_requests(url="https://api.sendpulse.com/instagram/contacts/send")[0]
    body = json.loads(sent.content)
    buttons = body["messages"][0]["message"]["buttons"]
    assert len(buttons) == 2
    assert buttons[0]["type"] == "url"
    assert buttons[0]["url"].startswith("https://t.me/")
    assert buttons[1]["type"] == "reply"
    assert buttons[1]["payload"] == "more_info"


async def test_send_comment_reply_fallback_on_403(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        method="POST",
        url="https://api.sendpulse.com/oauth/access_token",
        json={"access_token": "tok"},
    )
    httpx_mock.add_response(
        method="POST",
        url="https://api.sendpulse.com/instagram/comments/reply",
        status_code=403,
        text="paid feature",
    )
    httpx_mock.add_response(
        method="POST",
        url="https://api.sendpulse.com/instagram/contacts/send",
        json={"data": [{"id": "fallback_msg"}]},
    )

    provider = _make_provider()
    msg = OutgoingMessage(
        platform="instagram",
        external_user_id="user_1",
        text="Спасибо за комментарий!",
        reply_to_comment_id="comment_999",
    )
    result = await provider.send(msg)
    assert result == "fallback_msg"


async def test_send_returns_none_on_persistent_5xx(httpx_mock: HTTPXMock) -> None:
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

    provider = _make_provider()
    msg = OutgoingMessage(platform="instagram", external_user_id="u", text="x")
    result = await provider.send(msg)
    assert result is None


# ---- poll_new_events ----


async def test_poll_returns_events_and_advances_cursor(
    httpx_mock: HTTPXMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SENDPULSE_BOT_ID", "bot_test_1")
    from app.config import get_settings

    get_settings.cache_clear()

    httpx_mock.add_response(
        method="POST",
        url="https://api.sendpulse.com/oauth/access_token",
        json={"access_token": "tok"},
    )
    httpx_mock.add_response(
        method="GET",
        url=_MESSAGES_RE,
        json={
            "data": [
                {
                    "id": "msg_a",
                    "contact": {"id": "u_1", "username": "anna"},
                    "text": "hi",
                    "created_at": "2026-05-15T14:00:00Z",
                    "direction": "in",
                },
            ]
        },
    )
    httpx_mock.add_response(
        method="GET",
        url=_COMMENTS_RE,
        status_code=403,
        text="paid feature",
    )

    provider = _make_provider()
    events = await provider.poll_new_events()
    assert len(events) == 1
    assert events[0].external_user_id == "u_1"
    assert events[0].event_type == "message"

    from app.providers.sendpulse_cursor import get_cursor

    new_cursor = await get_cursor("bot_test_1")
    assert new_cursor.year == 2026


async def test_poll_filters_outgoing_messages(
    httpx_mock: HTTPXMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Polling should NOT return our own outgoing messages as IncomingEvent."""
    monkeypatch.setenv("SENDPULSE_BOT_ID", "bot_test_2")
    from app.config import get_settings

    get_settings.cache_clear()

    httpx_mock.add_response(
        method="POST",
        url="https://api.sendpulse.com/oauth/access_token",
        json={"access_token": "tok"},
    )
    httpx_mock.add_response(
        method="GET",
        url=_MESSAGES_RE,
        json={
            "data": [
                {
                    "id": "msg_in",
                    "contact": {"id": "u_1"},
                    "text": "hi",
                    "created_at": "2026-05-15T14:00:00Z",
                    "direction": "in",
                },
                {
                    "id": "msg_out",
                    "contact": {"id": "u_1"},
                    "text": "bot reply",
                    "created_at": "2026-05-15T14:00:30Z",
                    "direction": "out",
                },
            ]
        },
    )
    httpx_mock.add_response(
        method="GET",
        url=_COMMENTS_RE,
        status_code=403,
    )

    provider = _make_provider()
    events = await provider.poll_new_events()
    assert len(events) == 1
    assert events[0].external_event_id == "msg_in"


# ---- parse_webhook ----


def test_parse_webhook_no_secret_returns_empty() -> None:
    provider = SendPulseProvider("cid", "csecret", webhook_secret="")
    result = asyncio.run(provider.parse_webhook(b"[]", {"x-signature": "anything"}))
    assert result == []


async def test_parse_webhook_invalid_signature() -> None:
    provider = _make_provider()
    body = b'[{"type": "message", "id": "x"}]'
    result = await provider.parse_webhook(body, {"x-signature": "totally_wrong"})
    assert result == []


async def test_parse_webhook_valid_signature() -> None:
    provider = _make_provider()
    body = json.dumps(
        [
            {
                "type": "message",
                "id": "msg_w_1",
                "contact": {"id": "u_w", "username": "x"},
                "text": "from webhook",
                "created_at": "2026-05-15T14:00:00Z",
            }
        ]
    ).encode()
    signature = hmac.new(b"test_wsecret", body, hashlib.sha256).hexdigest()

    events = await provider.parse_webhook(body, {"x-signature": signature})
    assert len(events) == 1
    assert events[0].external_user_id == "u_w"
