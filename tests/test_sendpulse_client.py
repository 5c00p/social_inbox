"""Tests for low-level SendPulse HTTP client."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from pytest_httpx import HTTPXMock

from app.providers.sendpulse_client import (
    SendPulseAPIError,
    SendPulseAuthError,
    SendPulseClient,
)
from app.repos.redis_client import get_redis


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
        json={"access_token": "test_token_abc", "expires_in": 3600},
    )
    client = SendPulseClient("cid", "csecret")
    token = await client._get_token()
    assert token == "test_token_abc"


async def test_oauth_uses_cache(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        method="POST",
        url="https://api.sendpulse.com/oauth/access_token",
        json={"access_token": "cached_token"},
    )
    client = SendPulseClient("cid", "csecret")
    t1 = await client._get_token()
    # Second call hits the Redis cache; no new HTTP request expected.
    t2 = await client._get_token()
    assert t1 == t2 == "cached_token"


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


async def test_request_retries_on_500(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        method="POST",
        url="https://api.sendpulse.com/oauth/access_token",
        json={"access_token": "tok"},
    )
    httpx_mock.add_response(
        method="GET",
        url="https://api.sendpulse.com/instagram/messages",
        status_code=500,
        text="server error",
    )
    httpx_mock.add_response(
        method="GET",
        url="https://api.sendpulse.com/instagram/messages",
        json={"data": []},
    )
    client = SendPulseClient("cid", "csecret")
    result = await client.request("GET", "/instagram/messages", max_retries=3)
    assert result == {"data": []}


async def test_request_no_retry_on_403(httpx_mock: HTTPXMock) -> None:
    """Free tier limitation — don't waste retries."""
    httpx_mock.add_response(
        method="POST",
        url="https://api.sendpulse.com/oauth/access_token",
        json={"access_token": "tok"},
    )
    httpx_mock.add_response(
        method="GET",
        url="https://api.sendpulse.com/instagram/comments",
        status_code=403,
        text="forbidden — paid plan required",
    )
    client = SendPulseClient("cid", "csecret")
    with pytest.raises(SendPulseAPIError) as exc_info:
        await client.request("GET", "/instagram/comments")
    assert exc_info.value.status == 403


async def test_request_refreshes_token_on_401(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        method="POST",
        url="https://api.sendpulse.com/oauth/access_token",
        json={"access_token": "first_token"},
    )
    httpx_mock.add_response(
        method="GET",
        url="https://api.sendpulse.com/instagram/messages",
        status_code=401,
    )
    httpx_mock.add_response(
        method="POST",
        url="https://api.sendpulse.com/oauth/access_token",
        json={"access_token": "refreshed_token"},
    )
    httpx_mock.add_response(
        method="GET",
        url="https://api.sendpulse.com/instagram/messages",
        json={"data": [{"id": 1}]},
    )
    client = SendPulseClient("cid", "csecret")
    result = await client.request("GET", "/instagram/messages")
    assert result == {"data": [{"id": 1}]}
