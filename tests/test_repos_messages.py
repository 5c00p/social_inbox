"""Tests for app.repos.messages."""
from __future__ import annotations

import pytest

from app.repos import conversations, messages, users


@pytest.mark.asyncio
async def test_insert_and_get_recent(db) -> None:  # type: ignore[no-untyped-def]
    user = await users.create(
        provider_name="sendpulse", platform="instagram", external_id="msg_user",
    )
    conv = await conversations.create(user["id"], "instagram")

    m1 = await messages.insert(
        conversation_id=conv["id"], direction="in",
        text="Привет!", external_message_id="ext_1",
    )
    m2 = await messages.insert(
        conversation_id=conv["id"], direction="out",
        text="Здравствуй!", external_message_id="ext_2",
    )
    assert m1 is not None
    assert m2 is not None

    recent = await messages.get_recent(conv["id"])
    assert len(recent) == 2
    assert recent[0]["text"] == "Привет!"  # oldest first


@pytest.mark.asyncio
async def test_insert_idempotent_on_external_id(db) -> None:  # type: ignore[no-untyped-def]
    user = await users.create(
        provider_name="sendpulse", platform="instagram", external_id="dup_user",
    )
    conv = await conversations.create(user["id"], "instagram")

    m1 = await messages.insert(
        conversation_id=conv["id"], direction="in",
        text="Original", external_message_id="dup_1",
    )
    m2 = await messages.insert(
        conversation_id=conv["id"], direction="in",
        text="Duplicate attempt", external_message_id="dup_1",
    )
    assert m1 is not None
    assert m2 is None  # silent dedup
