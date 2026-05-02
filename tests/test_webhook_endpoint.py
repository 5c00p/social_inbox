"""Tests for /webhooks/{provider} endpoint."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest
from httpx import AsyncClient

from app.models.events import IncomingEvent
from tests.fakes.fake_provider import FakeProvider


def _make_event(
    external_event_id: str = "evt_1",
    external_user_id: str = "user_1",
    text: str = "Hello",
) -> IncomingEvent:
    return IncomingEvent(
        provider="sendpulse",
        platform="instagram",
        event_type="message",
        external_user_id=external_user_id,
        external_event_id=external_event_id,
        username="alice",
        text=text,
        occurred_at=datetime(2026, 4, 30, 12, 0, tzinfo=UTC),
    )


@pytest.mark.asyncio
async def test_webhook_returns_200_on_empty_events(
    client: AsyncClient, fake_provider: FakeProvider, db,
) -> None:
    # No events queued — provider returns []
    response = await client.post("/webhooks/sendpulse", json={})
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_webhook_returns_200_on_invalid_signature(
    client: AsyncClient, fake_provider: FakeProvider, db,
) -> None:
    """Critical contract: NEVER return non-200 from webhook."""
    fake_provider.signature_valid = False
    fake_provider.queue_event(_make_event())

    response = await client.post("/webhooks/sendpulse", json={"foo": "bar"})
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_webhook_records_event_in_log(
    client: AsyncClient, fake_provider: FakeProvider, db,
) -> None:
    event = _make_event(external_event_id="evt_log_1")
    fake_provider.queue_event(event)

    response = await client.post("/webhooks/sendpulse", json={})
    assert response.status_code == 200

    row = await db.fetchrow(
        "SELECT * FROM events_log WHERE external_event_id = $1",
        "evt_log_1",
    )
    assert row is not None
    assert row["provider_name"] == "sendpulse"
    assert row["event_type"] == "message"
    assert row["signature_valid"] is True


@pytest.mark.asyncio
async def test_webhook_duplicate_event_id_does_not_crash(
    client: AsyncClient, fake_provider: FakeProvider, db,
) -> None:
    event = _make_event(external_event_id="evt_dup")
    fake_provider.queue_event(event)
    await client.post("/webhooks/sendpulse", json={})

    # Second time — same event_id, should be silently dropped at events_log unique index
    fake_provider.queue_event(event)
    response = await client.post("/webhooks/sendpulse", json={})
    assert response.status_code == 200

    rows = await db.fetch(
        "SELECT * FROM events_log WHERE external_event_id = $1",
        "evt_dup",
    )
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_webhook_handles_parse_exception(
    client: AsyncClient, fake_provider: FakeProvider, db,
) -> None:
    """If provider.parse_webhook raises, endpoint still returns 200."""

    class BrokenProvider(FakeProvider):
        async def parse_webhook(self, raw_body, headers):  # type: ignore[override]
            raise RuntimeError("intentional bug")

    from app.api.webhooks import _provider_dep
    from app.main import app

    broken = BrokenProvider()
    app.dependency_overrides[_provider_dep] = lambda: broken
    try:
        response = await client.post("/webhooks/sendpulse", json={})
        assert response.status_code == 200
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_webhook_get_verification_echoes_challenge(
    client: AsyncClient,
) -> None:
    """Meta-style verification: GET with hub.challenge → echo it back."""
    response = await client.get("/webhooks/sendpulse?hub.challenge=12345")
    assert response.status_code == 200
    assert response.json() == {"hub.challenge": "12345"}


@pytest.mark.asyncio
async def test_webhook_get_without_challenge(client: AsyncClient) -> None:
    response = await client.get("/webhooks/sendpulse")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
