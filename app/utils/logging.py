"""Structured logging via structlog.

Use:
    from app.utils.logging import get_logger
    log = get_logger(__name__)
    log.info("event", user_id=42, source="ig")
"""

from __future__ import annotations

import logging
import sys
from typing import Any, cast

import structlog

from app.config import get_settings


def _configure_structlog(json_output: bool, level: str) -> None:
    timestamper = structlog.processors.TimeStamper(fmt="iso", utc=True)
    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        timestamper,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    if json_output:
        renderer: Any = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=True)

    structlog.configure(
        processors=[*shared_processors, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(getattr(logging, level)),
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=getattr(logging, level),
    )


def setup_logging() -> None:
    """Initialise logging based on settings. Call once at app startup."""
    settings = get_settings()
    _configure_structlog(
        json_output=(settings.env == "prod"),
        level=settings.log_level,
    )


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    return cast(structlog.stdlib.BoundLogger, structlog.get_logger(name))
