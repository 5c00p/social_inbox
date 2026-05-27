"""Tests for SendPulseClient — real API endpoints."""

from __future__ import annotations

import json as _json
import re
from collections.abc import AsyncIterator

import pytest
from pytest_httpx import HTTPXMock

from app.providers.sendpulse_client import (
    SendPulseAPIError,
    SendPulseAuthError,
    SendPulseClient,
)
from app.repos.redis_client import get_redis

# pytest-httpx 0.35 takes a compiled regex for prefix-style matching.
_CHATS_RE = re.compile(r"^https://api\.sendpulse\.com/instagram/chats")


@pytest.fixture(autouse=True)
async def _clear_token_cache() -> AsyncIterator[None]:
    redis = await get_redis()
    await redis.delete("sendpulse:access_token")
    yield
    await redis.delete("sendpulse:access_token")


async def test_oauth_fetches_token(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        method="POST",
        url="https://api.sendpulse.com/oauth/access_token",
        json={"access_token": "test_token", "expires_in": 3600},
    )
    client = SendPulseClient("cid", "csecret")
    token = await client._get_token()
    assert token == "test_token"


async def test_list_chats_returns_data(httpx_mock: HTTPXMock) -> None:
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
            "data": [{"inbox_last_message": {"id": "msg1"}, "contact": {}}],
            "meta": {"total": 1, "limit": 50},
        },
    )
    client = SendPulseClient("cid", "csecret")
    result = await client.list_chats("bot_xyz")
    assert result["success"] is True
    assert len(result["data"]) == 1
    assert result["data"][0]["inbox_last_message"]["id"] == "msg1"


async def test_list_chats_next_uses_absolute_url(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        method="POST",
        url="https://api.sendpulse.com/oauth/access_token",
        json={"access_token": "tok"},
    )
    next_url = "https://api.sendpulse.com/api/instagram/chats?bot_id=bot_xyz&page=2&jwt=x"
    httpx_mock.add_response(
        method="GET",
        url=next_url,
        json={"success": True, "data": []},
    )
    client = SendPulseClient("cid", "csecret")
    result = await client.list_chats_next(next_url)
    assert result["success"] is True


async def test_send_message_posts_correct_body(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        method="POST",
        url="https://api.sendpulse.com/oauth/access_token",
        json={"access_token": "tok"},
    )
    httpx_mock.add_response(
        method="POST",
        url="https://api.sendpulse.com/instagram/contacts/send",
        json={"success": True, "data": {"id": "sent_msg_1"}},
    )
    client = SendPulseClient("cid", "csecret")
    result = await client.send_message(
        "contact_xyz",
        [{"type": "text", "message": {"text": "Hi!"}}],
    )
    assert result["data"]["id"] == "sent_msg_1"

    sent = httpx_mock.get_requests(
        url="https://api.sendpulse.com/instagram/contacts/send",
    )[0]
    body = _json.loads(sent.content)
    assert body == {
        "contact_id": "contact_xyz",
        "messages": [{"type": "text", "message": {"text": "Hi!"}}],
    }


async def test_403_not_retried(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        method="POST",
        url="https://api.sendpulse.com/oauth/access_token",
        json={"access_token": "tok"},
    )
    httpx_mock.add_response(
        method="GET",
        url=_CHATS_RE,
        status_code=403,
        text="paid feature",
    )
    client = SendPulseClient("cid", "csecret")
    with pytest.raises(SendPulseAPIError) as exc:
        await client.list_chats("bot_xyz")
    assert exc.value.status == 403


async def test_401_refreshes_token_once(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        method="POST",
        url="https://api.sendpulse.com/oauth/access_token",
        json={"access_token": "first_token"},
    )
    httpx_mock.add_response(
        method="GET",
        url=_CHATS_RE,
        status_code=401,
    )
    httpx_mock.add_response(
        method="POST",
        url="https://api.sendpulse.com/oauth/access_token",
        json={"access_token": "refreshed_token"},
    )
    httpx_mock.add_response(
        method="GET",
        url=_CHATS_RE,
        json={"success": True, "data": []},
    )
    client = SendPulseClient("cid", "csecret")
    result = await client.list_chats("bot_xyz")
    assert result["success"] is True


async def test_oauth_failed_raises(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        method="POST",
        url="https://api.sendpulse.com/oauth/access_token",
        status_code=401,
        text="invalid credentials",
    )
    client = SendPulseClient("bad", "bad")
    with pytest.raises(SendPulseAuthError):
        await client._get_token()
