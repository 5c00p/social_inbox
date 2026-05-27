"""E2E: SendPulse polling → events_log → worker → user/conversation row."""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import asyncpg
import pytest
from pytest_httpx import HTTPXMock

from app.providers import reset_provider
from app.providers.sendpulse_cursor import set_cursor
from app.repos import users
from app.repos.redis_client import get_redis
from app.workers.tasks_messages import process_incoming_event
from app.workers.tasks_sendpulse import sendpulse_poll_tick

_CHATS_RE = re.compile(r"^https://api\.sendpulse\.com/instagram/chats")


@pytest.fixture(autouse=True)
async def _setup(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[None]:
    monkeypatch.setenv("MESSAGING_PROVIDER", "sendpulse")
    monkeypatch.setenv("SENDPULSE_POLLING_ENABLED", "true")
    monkeypatch.setenv("SENDPULSE_BOT_ID", "bot_e2e")
    monkeypatch.setenv("SENDPULSE_CLIENT_ID", "cid")
    monkeypatch.setenv("SENDPULSE_CLIENT_SECRET", "csecret")
    from app.config import get_settings

    get_settings.cache_clear()
    reset_provider()

    redis = await get_redis()
    keys = await redis.keys("sendpulse:*")
    if keys:
        await redis.delete(*keys)
    yield
    reset_provider()


async def test_polling_picks_up_real_dm_and_processes_it(
    httpx_mock: HTTPXMock,
    db: asyncpg.Connection,
) -> None:
    """Full pipeline test against real /chats response shape."""
    await set_cursor("bot_e2e", datetime(2026, 5, 1, tzinfo=UTC))

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
                {
                    "inbox_last_message": {
                        "contact_id": "e2e_contact_1",
                        "bot_id": "bot_e2e",
                        "type": "text",
                        "direction": 1,
                        "created_at": "2026-05-25T22:50:43+00:00",
                        "id": "e2e_msg_1",
                        "data": {
                            "text": "Привет!",
                            "is_echo": False,
                        },
                    },
                    "inbox_unread": 1,
                    "contact": {
                        "id": "e2e_contact_1",
                        "channel_data": {
                            "name": "Test User",
                            "user_name": "test_user_1",
                            "first_name": "Test",
                        },
                    },
                }
            ],
            "meta": {"total": 1, "limit": 50},
        },
    )
    # Worker will fire welcome scenario → provider.send() → contacts/send
    httpx_mock.add_response(
        method="POST",
        url="https://api.sendpulse.com/instagram/contacts/send",
        json={"success": True, "data": {"id": "e2e_outgoing_1"}},
    )

    # 1. Polling tick
    await sendpulse_poll_tick({})

    # 2. events_log got a row
    log_row = await db.fetchrow(
        "SELECT * FROM events_log WHERE external_event_id = 'e2e_msg_1'",
    )
    assert log_row is not None
    assert log_row["processed_at"] is None

    # 3. Drive the worker directly (in real life arq dequeues)
    from app.models.events import IncomingEvent

    event = IncomingEvent(
        provider="sendpulse",
        platform="instagram",
        event_type="message",
        external_user_id="e2e_contact_1",
        external_event_id="e2e_msg_1",
        username="test_user_1",
        full_name="Test User",
        text="Привет!",
        occurred_at=datetime(2026, 5, 25, 22, 50, 43, tzinfo=UTC),
        raw_payload={},
    )
    await process_incoming_event({}, event.model_dump(mode="json"), log_row["id"])

    # 4. User created
    user = await users.get_by_external("sendpulse", "instagram", "e2e_contact_1")
    assert user is not None
    assert user["username"] == "test_user_1"


async def test_polling_idempotent_on_repeat(
    httpx_mock: HTTPXMock,
    db: asyncpg.Connection,
) -> None:
    """Same message ID twice → only one events_log row."""
    await set_cursor("bot_e2e", datetime(2026, 5, 1, tzinfo=UTC))

    same_chat_payload = {
        "success": True,
        "data": [
            {
                "inbox_last_message": {
                    "contact_id": "idem_contact",
                    "type": "text",
                    "direction": 1,
                    "created_at": "2026-05-25T22:00:00+00:00",
                    "id": "idem_msg_1",
                    "data": {"text": "x", "is_echo": False},
                },
                "contact": {
                    "id": "idem_contact",
                    "channel_data": {"user_name": "x"},
                },
            }
        ],
    }

    # First poll
    httpx_mock.add_response(
        method="POST",
        url="https://api.sendpulse.com/oauth/access_token",
        json={"access_token": "tok"},
    )
    httpx_mock.add_response(
        method="GET",
        url=_CHATS_RE,
        json=same_chat_payload,
    )
    await sendpulse_poll_tick({})

    # Roll cursor BACK to before the message — simulate broken cursor scenario
    await set_cursor("bot_e2e", datetime(2026, 5, 1, tzinfo=UTC))

    # Second poll — same data
    httpx_mock.add_response(
        method="GET",
        url=_CHATS_RE,
        json=same_chat_payload,
    )
    await sendpulse_poll_tick({})

    # Only one row in events_log (dedup via UNIQUE on external_event_id)
    rows = await db.fetch(
        "SELECT * FROM events_log WHERE external_event_id = 'idem_msg_1'",
    )
    assert len(rows) == 1
