"""Health and readiness endpoints."""
from __future__ import annotations

from fastapi import APIRouter, status
from fastapi.responses import JSONResponse

from app.repos.pool import ping as pg_ping

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict[str, str]:
    """Liveness probe — returns ok if process is up."""
    return {"status": "ok"}


@router.get("/ready")
async def ready() -> JSONResponse:
    """Readiness probe — checks Postgres connectivity.

    Returns 200 + {"status": "ready"} when DB is reachable.
    Returns 503 + {"status": "not_ready", "postgres": "down"} otherwise.
    """
    pg_ok = await pg_ping()
    if pg_ok:
        return JSONResponse(content={"status": "ready", "postgres": "up"})
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content={"status": "not_ready", "postgres": "down"},
    )
