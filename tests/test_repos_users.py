"""Tests for app.repos.users."""
from __future__ import annotations

import pytest

from app.repos import users


@pytest.mark.asyncio
async def test_create_and_get_by_external(db) -> None:  # type: ignore[no-untyped-def]
    user = await users.create(
        provider_name="sendpulse",
        platform="instagram",
        external_id="ig_123",
        username="test_user",
        full_name="Test User",
    )
    assert user["id"] is not None
    assert user["short_id"] is not None
    assert len(user["short_id"]) == 8
    assert user["deleted_at"] is None

    fetched = await users.get_by_external("sendpulse", "instagram", "ig_123")
    assert fetched is not None
    assert fetched["id"] == user["id"]


@pytest.mark.asyncio
async def test_get_by_short_id(db) -> None:  # type: ignore[no-untyped-def]
    user = await users.create(
        provider_name="sendpulse",
        platform="instagram",
        external_id="ig_short_test",
    )
    fetched = await users.get_by_short_id(user["short_id"])
    assert fetched is not None
    assert fetched["external_id"] == "ig_short_test"


@pytest.mark.asyncio
async def test_short_id_is_unique_across_users(db) -> None:  # type: ignore[no-untyped-def]
    u1 = await users.create(
        provider_name="sendpulse", platform="instagram", external_id="a",
    )
    u2 = await users.create(
        provider_name="sendpulse", platform="instagram", external_id="b",
    )
    assert u1["short_id"] != u2["short_id"]


@pytest.mark.asyncio
async def test_get_unknown_user_returns_none(db) -> None:  # type: ignore[no-untyped-def]
    result = await users.get_by_external("sendpulse", "instagram", "nonexistent")
    assert result is None


@pytest.mark.asyncio
async def test_soft_delete_hides_user(db) -> None:  # type: ignore[no-untyped-def]
    from datetime import UTC, datetime

    user = await users.create(
        provider_name="sendpulse", platform="instagram", external_id="to_delete",
    )
    await users.soft_delete(user["id"], datetime.now(UTC))

    found = await users.get_by_external("sendpulse", "instagram", "to_delete")
    assert found is None  # filtered out by `deleted_at IS NULL`
