"""Helper to enqueue events from FastAPI handlers into arq queue."""
from __future__ import annotations

from arq import create_pool
from arq.connections import ArqRedis, RedisSettings

from app.config import get_settings
from app.models.events import IncomingEvent
from app.utils.logging import get_logger

log = get_logger(__name__)

_arq: ArqRedis | None = None


async def get_arq() -> ArqRedis:
    """Return arq connection pool, creating on first call."""
    global _arq
    if _arq is None:
        settings = get_settings()
        _arq = await create_pool(RedisSettings.from_dsn(settings.redis_url))
        log.info("arq_pool_created")
    return _arq


async def close_arq() -> None:
    global _arq
    if _arq is not None:
        await _arq.aclose()
        _arq = None


async def enqueue_event(event: IncomingEvent, log_id: int) -> None:
    """Enqueue an IncomingEvent for the worker to process.

    Args:
        event: the parsed event
        log_id: id of the row in events_log (so worker can mark it processed)
    """
    arq = await get_arq()
    await arq.enqueue_job(
        "process_incoming_event",
        event.model_dump(mode="json"),
        log_id,
    )
