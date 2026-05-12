"""Health and readiness endpoints.

Three endpoints for different consumers:

- /health         — process liveness (always 200 if process alive)
- /ready          — full readiness check: postgres + redis + worker heartbeat
                    Used by uptime monitoring, Docker healthcheck.
- /ready/quick    — fast readiness: postgres + redis only.
                    Used by load balancers (frequent calls, lower overhead).
"""
from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, status
from fastapi.responses import JSONResponse

from app.repos.pool import ping as pg_ping
from app.repos.redis_client import ping as redis_ping
from app.workers.heartbeat import heartbeat_age_seconds

router = APIRouter(tags=["health"])

WORKER_STALE_THRESHOLD_SECONDS = 180  # 3 minutes


@router.get("/health")
async def health() -> dict[str, str]:
    """Liveness probe — returns ok if process is up."""
    return {"status": "ok"}


@router.get("/ready/quick")
async def ready_quick() -> JSONResponse:
    """Quick readiness — postgres + redis only.

    Use for high-frequency LB checks (every few seconds).
    """
    pg_ok = await pg_ping()
    redis_ok = await redis_ping()
    body = {
        "status": "ready" if (pg_ok and redis_ok) else "not_ready",
        "postgres": "up" if pg_ok else "down",
        "redis": "up" if redis_ok else "down",
    }
    code = status.HTTP_200_OK if (pg_ok and redis_ok) else status.HTTP_503_SERVICE_UNAVAILABLE
    return JSONResponse(status_code=code, content=body)


@router.get("/ready")
async def ready() -> JSONResponse:
    """Full readiness — postgres + redis + worker heartbeat.

    Use for monitoring / Docker healthcheck (low frequency, every 30s+).
    Returns 503 if any component is down.
    """
    pg_ok = await pg_ping()
    redis_ok = await redis_ping()

    worker_age = await heartbeat_age_seconds()
    worker_ok = worker_age is not None and worker_age < WORKER_STALE_THRESHOLD_SECONDS

    body = {
        "status": "ready" if (pg_ok and redis_ok and worker_ok) else "not_ready",
        "postgres": "up" if pg_ok else "down",
        "redis": "up" if redis_ok else "down",
        "worker": {
            "status": "up" if worker_ok else "down",
            "heartbeat_age_seconds": worker_age,
        },
        "checked_at": datetime.now(UTC).isoformat(),
    }
    all_ok = pg_ok and redis_ok and worker_ok
    code = status.HTTP_200_OK if all_ok else status.HTTP_503_SERVICE_UNAVAILABLE
    return JSONResponse(status_code=code, content=body)
