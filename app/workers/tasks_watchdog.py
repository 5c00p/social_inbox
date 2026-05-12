"""Watchdog and reporting tasks running on cron schedule.

watchdog_check (every minute):
- Verify postgres ping
- Verify worker heartbeat is fresh
  (this runs in worker itself, so heartbeat being stale here is unusual —
   it would mean the heartbeat loop crashed)
- Trigger alerts via app.observability.alerts (with dedup)

daily_digest (09:00 local):
- Aggregate yesterday's metrics
- Send Telegram message to admin
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from app.observability.alerts import fire_alert
from app.repos.pool import get_pool
from app.repos.pool import ping as pg_ping
from app.services import notifications
from app.utils.logging import get_logger
from app.workers.heartbeat import heartbeat_age_seconds

log = get_logger(__name__)

WORKER_STALE_THRESHOLD_SECONDS = 180


async def watchdog_check(ctx: dict[str, Any]) -> None:
    """Run health checks and fire alerts on issues."""
    log.debug("watchdog_tick_start")

    # 1. Postgres
    pg_ok = await pg_ping()
    if not pg_ok:
        await fire_alert(
            "postgres_down",
            "Postgres unreachable from worker. "
            "Проверь VPS, контейнер postgres, диск.",
        )

    # 2. Worker heartbeat (sanity check — should always be fresh since we're worker ourselves)
    age = await heartbeat_age_seconds()
    if age is None or age >= WORKER_STALE_THRESHOLD_SECONDS:
        await fire_alert(
            "worker_dead",
            f"Heartbeat is stale: age={age}s "
            f"(threshold {WORKER_STALE_THRESHOLD_SECONDS}s). "
            "Heartbeat loop may have crashed.",
        )

    log.debug("watchdog_tick_done", postgres_ok=pg_ok, heartbeat_age=age)


async def daily_digest(ctx: dict[str, Any]) -> None:
    """Send daily summary to admin: yesterday's metrics."""
    log.info("daily_digest_running")

    pool = await get_pool()
    yesterday_start_utc = (datetime.now(UTC) - timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0,
    )
    yesterday_end_utc = yesterday_start_utc + timedelta(days=1)

    new_leads = await pool.fetchval(
        """
        SELECT COUNT(*)::int FROM social_users
        WHERE first_seen_at >= $1 AND first_seen_at < $2
          AND deleted_at IS NULL
        """,
        yesterday_start_utc, yesterday_end_utc,
    )

    handovers = await pool.fetchval(
        """
        SELECT COUNT(*)::int FROM conversations
        WHERE status IN ('handover_pending', 'handover_done')
          AND last_message_at >= $1 AND last_message_at < $2
        """,
        yesterday_start_utc, yesterday_end_utc,
    )

    tg_handovers = await pool.fetchval(
        """
        SELECT COUNT(*)::int FROM social_users
        WHERE tg_handover_at >= $1 AND tg_handover_at < $2
        """,
        yesterday_start_utc, yesterday_end_utc,
    )

    claude_calls = await pool.fetchval(
        """
        SELECT COUNT(*)::int FROM messages
        WHERE created_at >= $1 AND created_at < $2
          AND claude_used = TRUE
        """,
        yesterday_start_utc, yesterday_end_utc,
    )

    claude_blocked = await pool.fetchval(
        """
        SELECT COUNT(*)::int FROM messages
        WHERE created_at >= $1 AND created_at < $2
          AND safety_blocked = TRUE
        """,
        yesterday_start_utc, yesterday_end_utc,
    )

    date_label = yesterday_start_utc.strftime("%Y-%m-%d")
    text = (
        f"📊 *Сводка за {date_label}*\n\n"
        f"🆕 Новых лидов: *{new_leads}*\n"
        f"📲 Дошли до Telegram: *{tg_handovers}*\n"
        f"👤 Эскалации: *{handovers}*\n"
        f"🤖 Ответов через Claude: *{claude_calls}*\n"
        f"🛑 Заблокировано safety: *{claude_blocked}*\n\n"
        f"_Подробнее — в админке._"
    )
    await notifications.notify_admin(text)
    log.info(
        "daily_digest_sent",
        new_leads=new_leads,
        handovers=handovers,
        tg_handovers=tg_handovers,
        claude_calls=claude_calls,
    )
