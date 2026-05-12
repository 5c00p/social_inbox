"""Smoke tests for health endpoints."""
from __future__ import annotations

from httpx import AsyncClient

from app.workers.heartbeat import heartbeat_tick


async def test_health_returns_ok(client: AsyncClient) -> None:
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_ready_quick_checks_postgres_and_redis(client: AsyncClient) -> None:
    response = await client.get("/ready/quick")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["postgres"] == "up"
    assert body["redis"] == "up"


async def test_ready_full_when_worker_alive(client: AsyncClient) -> None:
    await heartbeat_tick()
    response = await client.get("/ready")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["postgres"] == "up"
    assert body["redis"] == "up"
    assert body["worker"]["status"] == "up"
