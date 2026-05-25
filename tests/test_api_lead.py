"""Tests for /api/lead/{short_id} endpoint."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import asyncpg
import pytest
from httpx import AsyncClient

from app.repos import conversations, messages, users
from app.repos import scenarios as scenarios_repo
from app.repos.redis_client import get_redis

# Tests assume INTERNAL_API_TOKEN='test-token' (set in conftest.py).
VALID_TOKEN = "test-token"
INVALID_TOKEN = "wrong-token"


async def _make_user_with_history(
    external_id: str = "lead_user_1",
    full_name: str = "Маша Петрова",
    username: str = "masha_p",
    *,
    with_outgoing: bool = True,
) -> Any:
    """Create a user with one incoming + optional outgoing message tied to default_welcome."""
    user = await users.create(
        provider_name="sendpulse",
        platform="instagram",
        external_id=external_id,
        username=username,
        full_name=full_name,
    )
    conv = await conversations.create(user["id"], "instagram")

    await messages.insert(
        conversation_id=conv["id"],
        direction="in",
        text="Привет",
        external_message_id=f"in_{external_id}",
    )

    if with_outgoing:
        scenario = await scenarios_repo.get_by_name("default_welcome")
        assert scenario is not None
        await messages.insert(
            conversation_id=conv["id"],
            direction="out",
            text=f"Welcome with deep-link to {user['short_id']}",
            scenario_id=scenario["id"],
            external_message_id=f"out_{external_id}",
        )

    return user


@pytest.fixture(autouse=True)
async def _clear_rate_limit() -> AsyncIterator[None]:
    """Clear API rate-limit keys between tests to avoid 429 from neighbours."""
    redis = await get_redis()
    keys = await redis.keys("rl:api:*")
    if keys:
        await redis.delete(*keys)
    yield


async def test_get_lead_returns_full_context(client: AsyncClient, db: asyncpg.Connection) -> None:
    user = await _make_user_with_history("lead_full_1")

    response = await client.get(
        f"/api/lead/{user['short_id']}",
        headers={"X-Internal-Token": VALID_TOKEN},
    )
    assert response.status_code == 200

    body = response.json()
    assert body["user"]["platform"] == "instagram"
    assert body["user"]["username"] == "masha_p"
    assert body["user"]["full_name"] == "Маша Петрова"
    assert body["scenario"] == "purify"  # from default_welcome metadata
    assert len(body["recent_messages"]) == 2
    assert body["recent_messages"][0]["direction"] == "in"
    assert body["recent_messages"][1]["direction"] == "out"


async def test_get_lead_unknown_scenario_when_no_outgoing(
    client: AsyncClient, db: asyncpg.Connection
) -> None:
    user = await _make_user_with_history("lead_no_out", with_outgoing=False)

    response = await client.get(
        f"/api/lead/{user['short_id']}",
        headers={"X-Internal-Token": VALID_TOKEN},
    )
    assert response.status_code == 200
    assert response.json()["scenario"] == "unknown"


async def test_get_lead_returns_401_without_token(
    client: AsyncClient, db: asyncpg.Connection
) -> None:
    user = await _make_user_with_history("lead_no_token")

    response = await client.get(f"/api/lead/{user['short_id']}")
    assert response.status_code == 401
    assert "Missing X-Internal-Token" in response.json()["detail"]


async def test_get_lead_returns_401_with_wrong_token(
    client: AsyncClient, db: asyncpg.Connection
) -> None:
    user = await _make_user_with_history("lead_bad_token")

    response = await client.get(
        f"/api/lead/{user['short_id']}",
        headers={"X-Internal-Token": INVALID_TOKEN},
    )
    assert response.status_code == 401


async def test_get_lead_returns_404_for_unknown_short_id(client: AsyncClient) -> None:
    response = await client.get(
        "/api/lead/nonexistent",
        headers={"X-Internal-Token": VALID_TOKEN},
    )
    assert response.status_code == 404


async def test_get_lead_returns_404_for_soft_deleted_user(
    client: AsyncClient, db: asyncpg.Connection
) -> None:
    user = await _make_user_with_history("lead_deleted")
    await users.soft_delete(user["id"], datetime.now(UTC))

    response = await client.get(
        f"/api/lead/{user['short_id']}",
        headers={"X-Internal-Token": VALID_TOKEN},
    )
    assert response.status_code == 404


async def test_get_lead_messages_chronological_order(
    client: AsyncClient, db: asyncpg.Connection
) -> None:
    user = await users.create(
        provider_name="sendpulse",
        platform="instagram",
        external_id="lead_order",
    )
    conv = await conversations.create(user["id"], "instagram")

    for i in range(3):
        await messages.insert(
            conversation_id=conv["id"],
            direction="in" if i % 2 == 0 else "out",
            text=f"msg-{i}",
            external_message_id=f"order_{i}",
        )

    response = await client.get(
        f"/api/lead/{user['short_id']}",
        headers={"X-Internal-Token": VALID_TOKEN},
    )
    msgs = response.json()["recent_messages"]
    assert len(msgs) == 3
    texts = [m["text"] for m in msgs]
    assert texts == ["msg-0", "msg-1", "msg-2"]


async def test_get_lead_limits_to_10_messages(client: AsyncClient, db: asyncpg.Connection) -> None:
    user = await users.create(
        provider_name="sendpulse",
        platform="instagram",
        external_id="lead_limit",
    )
    conv = await conversations.create(user["id"], "instagram")

    for i in range(15):
        await messages.insert(
            conversation_id=conv["id"],
            direction="in",
            text=f"msg-{i:02d}",
            external_message_id=f"limit_{i:02d}",
        )

    response = await client.get(
        f"/api/lead/{user['short_id']}",
        headers={"X-Internal-Token": VALID_TOKEN},
    )
    msgs = response.json()["recent_messages"]
    assert len(msgs) == 10
    assert msgs[0]["text"] == "msg-05"
    assert msgs[-1]["text"] == "msg-14"


async def test_get_lead_response_schema_matches_contract(
    client: AsyncClient, db: asyncpg.Connection
) -> None:
    """Sanity: response keys exactly match the contract documented in CLAUDE.md § 9.2."""
    user = await _make_user_with_history("lead_schema")

    response = await client.get(
        f"/api/lead/{user['short_id']}",
        headers={"X-Internal-Token": VALID_TOKEN},
    )
    body = response.json()
    assert set(body.keys()) == {"user", "scenario", "recent_messages"}
    assert set(body["user"].keys()) == {"platform", "username", "full_name", "first_seen_at"}
    if body["recent_messages"]:
        assert set(body["recent_messages"][0].keys()) == {"direction", "text", "created_at"}


async def test_handover_records_tg_user_id(client: AsyncClient, db: asyncpg.Connection) -> None:
    user = await _make_user_with_history("handover_user_1")

    response = await client.post(
        f"/api/lead/{user['short_id']}/handover",
        json={"tg_user_id": 123456789},
        headers={"X-Internal-Token": VALID_TOKEN},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["tg_user_id"] == 123456789
    assert "handed_over_at" in body

    updated = await db.fetchrow(
        "SELECT tg_user_id, tg_handover_at FROM social_users WHERE id = $1",
        user["id"],
    )
    assert updated["tg_user_id"] == 123456789
    assert updated["tg_handover_at"] is not None


async def test_handover_idempotent(client: AsyncClient, db: asyncpg.Connection) -> None:
    user = await _make_user_with_history("handover_idem")

    r1 = await client.post(
        f"/api/lead/{user['short_id']}/handover",
        json={"tg_user_id": 111},
        headers={"X-Internal-Token": VALID_TOKEN},
    )
    assert r1.status_code == 200

    r2 = await client.post(
        f"/api/lead/{user['short_id']}/handover",
        json={"tg_user_id": 111},
        headers={"X-Internal-Token": VALID_TOKEN},
    )
    assert r2.status_code == 200


async def test_handover_404_for_unknown_short_id(client: AsyncClient) -> None:
    response = await client.post(
        "/api/lead/nonexistent/handover",
        json={"tg_user_id": 999},
        headers={"X-Internal-Token": VALID_TOKEN},
    )
    assert response.status_code == 404


async def test_handover_validates_positive_tg_user_id(
    client: AsyncClient, db: asyncpg.Connection
) -> None:
    user = await _make_user_with_history("handover_invalid")

    response = await client.post(
        f"/api/lead/{user['short_id']}/handover",
        json={"tg_user_id": 0},
        headers={"X-Internal-Token": VALID_TOKEN},
    )
    assert response.status_code == 422


async def test_handover_rejects_extra_fields(client: AsyncClient, db: asyncpg.Connection) -> None:
    user = await _make_user_with_history("handover_extra")

    response = await client.post(
        f"/api/lead/{user['short_id']}/handover",
        json={"tg_user_id": 123, "secret_field": "x"},
        headers={"X-Internal-Token": VALID_TOKEN},
    )
    assert response.status_code == 422


async def test_rate_limit_exceeded_returns_429(client: AsyncClient, db: asyncpg.Connection) -> None:
    user = await _make_user_with_history("rate_limit_user")

    for _ in range(60):
        r = await client.get(
            f"/api/lead/{user['short_id']}",
            headers={"X-Internal-Token": VALID_TOKEN},
        )
        assert r.status_code == 200

    r_throttled = await client.get(
        f"/api/lead/{user['short_id']}",
        headers={"X-Internal-Token": VALID_TOKEN},
    )
    assert r_throttled.status_code == 429
