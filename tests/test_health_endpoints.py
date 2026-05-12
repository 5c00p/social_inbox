"""Tests for /health, /ready, /ready/quick endpoints."""
from __future__ import annotations

from httpx import AsyncClient

from app.repos.redis_client import get_redis
from app.workers.heartbeat import HEARTBEAT_KEY, heartbeat_tick


async def test_health_always_ok(client: AsyncClient) -> None:
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_ready_quick_with_pg_redis_up(client: AsyncClient) -> None:
    response = await client.get("/ready/quick")
    assert response.status_code == 200
    body = response.json()
    assert body["postgres"] == "up"
    assert body["redis"] == "up"


async def test_ready_full_requires_worker_heartbeat(client: AsyncClient) -> None:
    redis = await get_redis()
    await redis.delete(HEARTBEAT_KEY)

    # No heartbeat → /ready returns 503
    r1 = await client.get("/ready")
    assert r1.status_code == 503
    body1 = r1.json()
    assert body1["worker"]["status"] == "down"

    # After heartbeat tick → 200
    await heartbeat_tick()
    r2 = await client.get("/ready")
    assert r2.status_code == 200
    body2 = r2.json()
    assert body2["worker"]["status"] == "up"
    assert body2["status"] == "ready"
