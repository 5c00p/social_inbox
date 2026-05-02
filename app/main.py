"""FastAPI application entry point."""
from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import health
from app.config import get_settings
from app.repos.pool import close_pool, run_migrations
from app.utils.logging import get_logger, setup_logging


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    setup_logging()
    log = get_logger(__name__)
    settings = get_settings()
    log.info("startup", env=settings.env, provider=settings.messaging_provider)

    await run_migrations()

    yield

    await close_pool()
    log.info("shutdown")


def create_app() -> FastAPI:
    app = FastAPI(
        title="social_inbox",
        version="0.1.0",
        description="Automated messaging service for Instagram/Facebook lead capture",
        lifespan=lifespan,
    )
    app.include_router(health.router)
    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
