"""E2E: polling → events_log → worker → user/conversation row."""

from __future__ import annotations

import re
from collections.abc import AsyncIterator

import asyncpg
import pytest
from pytest_httpx import HTTPXMock

from app.providers import reset_provider
from app.repos import users
from app.repos.redis_client import get_redis
from app.workers.tasks_messages import process_incoming_event
from app.workers.tasks_sendpulse import sendpulse_poll_tick

_MESSAGES_RE = re.compile(r"^https://api\.sendpulse\.com/instagram/messages")
_COMMENTS_RE = re.compile(r"^https://api\.sendpulse\.com/instagram/comments")


@pytest.fixture(autouse=True)
async def _setup(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[None]:
    monkeypatch.setenv("MESSAGING_PROVIDER", "sendpulse")
    monkeypatch.setenv("SENDPULSE_POLLING_ENABLED", "true")
    monkeypatch.setenv("SENDPULSE_BOT_ID", "bot_e2e")
    from app.config import get_settings

    get_settings.cache_clear()
    reset_provider()

    redis = await get_redis()
    keys = await redis.keys("sendpulse:*")
    if keys:
        await redis.delete(*keys)
    yield
    reset_provider()


async def test_polling_creates_user_via_pipeline(
    httpx_mock: HTTPXMock,
    db: asyncpg.Connection,
) -> None:
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
                    "id": "e2e_msg_1",
                    "contact": {"id": "e2e_user_1", "username": "anna", "name": "Anna"},
                    "text": "Привет",
                    "created_at": "2026-05-15T14:00:00Z",
                    "direction": "in",
                }
            ]
        },
    )
    httpx_mock.add_response(
        method="GET",
        url=_COMMENTS_RE,
        status_code=403,
    )
    # Welcome reply will go out — provider.send hits contacts/send
    httpx_mock.add_response(
        method="POST",
        url="https://api.sendpulse.com/instagram/contacts/send",
        json={"data": [{"id": "outgoing_sent_1"}]},
    )

    # 1. Poll
    await sendpulse_poll_tick({})

    # events_log row exists, not yet processed
    log_row = await db.fetchrow(
        "SELECT * FROM events_log WHERE external_event_id = 'e2e_msg_1'",
    )
    assert log_row is not None
    assert log_row["processed_at"] is None

    # 2. Drive the worker directly (in real life arq dequeues this).
    event_dict = {
        "provider": "sendpulse",
        "platform": "instagram",
        "event_type": "message",
        "external_user_id": "e2e_user_1",
        "external_event_id": "e2e_msg_1",
        "occurred_at": "2026-05-15T14:00:00+00:00",
        "text": "Привет",
        "username": "anna",
        "full_name": "Anna",
        "raw_payload": dict(log_row["payload"]) if isinstance(log_row["payload"], dict) else {},
    }
    await process_incoming_event({}, event_dict, log_row["id"])

    # 3. User created
    user = await users.get_by_external("sendpulse", "instagram", "e2e_user_1")
    assert user is not None
    assert user["username"] == "anna"
