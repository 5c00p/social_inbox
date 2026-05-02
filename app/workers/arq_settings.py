"""arq worker configuration.

Run worker:
    arq app.workers.arq_settings.WorkerSettings

Tasks defined here are auto-registered by arq.
"""
from __future__ import annotations

import asyncio
import contextlib
from typing import Any, ClassVar

from arq.connections import RedisSettings

from app.config import get_settings
from app.workers.heartbeat import heartbeat_tick
from app.workers.tasks_messages import process_incoming_event


def _redis_settings() -> RedisSettings:
    settings = get_settings()
    return RedisSettings.from_dsn(settings.redis_url)


class WorkerSettings:
    """Settings consumed by `arq` CLI runner."""

    redis_settings = _redis_settings()

    # Tasks
    functions: ClassVar[list[Any]] = [process_incoming_event]

    # Heartbeat registered via on_startup loop (see below)
    cron_jobs: ClassVar[list[Any]] = []

    # Concurrency
    max_jobs = 10
    job_timeout = 60      # seconds — webhook events should be fast
    keep_result = 60      # seconds to keep job result in Redis (for debugging)
    max_tries = 3         # exponential retry on exception

    # Logging
    log_results = True

    @staticmethod
    async def on_startup(ctx: dict[str, Any]) -> None:
        ctx["heartbeat_task"] = asyncio.create_task(_heartbeat_loop())

    @staticmethod
    async def on_shutdown(ctx: dict[str, Any]) -> None:
        task = ctx.get("heartbeat_task")
        if task:
            task.cancel()


async def _heartbeat_loop() -> None:
    """Background task: write timestamp to Redis every 60 seconds."""
    while True:
        with contextlib.suppress(Exception):
            await heartbeat_tick()
        await asyncio.sleep(60)
