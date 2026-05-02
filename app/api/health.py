"""Health and readiness endpoints."""
from __future__ import annotations

from fastapi import APIRouter, status
from fastapi.responses import JSONResponse

from app.repos.pool import ping as pg_ping
from app.repos.redis_client import ping as redis_ping

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict[str, str]:
    """Liveness probe — returns ok if process is up."""
    return {"status": "ok"}


@router.get("/ready")
async def ready() -> JSONResponse:
    """Readiness probe — checks Postgres and Redis."""
    pg_ok = await pg_ping()
    redis_ok = await redis_ping()
    body = {
        "status": "ready" if (pg_ok and redis_ok) else "not_ready",
        "postgres": "up" if pg_ok else "down",
        "redis": "up" if redis_ok else "down",
    }
    code = status.HTTP_200_OK if (pg_ok and redis_ok) else status.HTTP_503_SERVICE_UNAVAILABLE
    return JSONResponse(status_code=code, content=body)
