"""arq worker configuration.

Run worker:
    arq app.workers.arq_settings.WorkerSettings
"""

from __future__ import annotations

from typing import Any, ClassVar

from arq import cron
from arq.connections import RedisSettings

from app.config import get_settings
from app.workers.heartbeat import heartbeat_tick
from app.workers.tasks_messages import process_incoming_event
from app.workers.tasks_sendpulse import sendpulse_poll_tick
from app.workers.tasks_watchdog import daily_digest, watchdog_check


def _redis_settings() -> RedisSettings:
    settings = get_settings()
    return RedisSettings.from_dsn(settings.redis_url)


class WorkerSettings:
    """Settings consumed by `arq` CLI runner."""

    redis_settings = _redis_settings()

    functions: ClassVar[list[Any]] = [
        process_incoming_event,
        watchdog_check,
        daily_digest,
        sendpulse_poll_tick,
    ]

    cron_jobs: ClassVar[list[Any]] = [
        # Watchdog: every minute
        cron(watchdog_check, minute=set(range(60)), run_at_startup=False),
        # Daily digest: 09:00 Europe/Vilnius (UTC+2 winter, UTC+3 summer).
        # We schedule by UTC; pick 07:00 UTC ≈ 09:00–10:00 local.
        cron(daily_digest, hour={7}, minute={0}, run_at_startup=False),
        # SendPulse poller: every 30 seconds (sub-minute via second=).
        # Static schedule — if SENDPULSE_POLLING_INTERVAL_SECONDS changes from
        # 30, update this set accordingly (e.g. {0,15,30,45} for 15s).
        # Task is a no-op when polling disabled or non-sendpulse provider.
        cron(sendpulse_poll_tick, second={0, 30}, run_at_startup=True),
    ]

    max_jobs = 10
    job_timeout = 60
    keep_result = 60
    max_tries = 3
    log_results = True

    @staticmethod
    async def on_startup(ctx: dict[str, Any]) -> None:
        import asyncio

        from app.observability.sentry import init_sentry
        from app.utils.logging import setup_logging

        setup_logging()
        init_sentry("worker")

        ctx["heartbeat_task"] = asyncio.create_task(_heartbeat_loop())

    @staticmethod
    async def on_shutdown(ctx: dict[str, Any]) -> None:
        task = ctx.get("heartbeat_task")
        if task:
            task.cancel()


async def _heartbeat_loop() -> None:
    """Background task: write timestamp to Redis every 60 seconds."""
    import asyncio
    import contextlib

    while True:
        with contextlib.suppress(Exception):
            await heartbeat_tick()
        await asyncio.sleep(60)
