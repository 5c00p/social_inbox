"""Sentry initialization for FastAPI app and arq worker.

We use a single init() function called from both entry points (main.py
and arq_settings.on_startup). Sentry SDK is idempotent — calling init twice
in the same process is safe.

Config:
- DSN from settings.sentry_dsn (skip init if empty)
- Environment from settings.env (dev/prod)
- 10% trace sample rate to stay within free-tier quota
- 0% profile sample rate (profiling not needed for our scale)
- before_send filter to drop noisy errors (rate limit hits, etc.)
"""
from __future__ import annotations

from typing import Any

import sentry_sdk
from sentry_sdk.integrations.asyncio import AsyncioIntegration
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sentry_sdk.integrations.starlette import StarletteIntegration

from app.config import get_settings
from app.utils.logging import get_logger

log = get_logger(__name__)

# Errors we want to drop before sending to Sentry — they're operational, not bugs.
_NOISY_ERROR_FRAGMENTS = (
    "rate limit",
    "rate_limit_exceeded",
    "X-Internal-Token",  # 401s from probing
)


def _before_send(event: dict[str, Any], hint: dict[str, Any]) -> dict[str, Any] | None:
    """Filter out noisy errors before they consume our Sentry quota."""
    exc_info = hint.get("exc_info")
    if exc_info:
        exc_value = exc_info[1]
        text = str(exc_value).lower()
        if any(frag in text for frag in _NOISY_ERROR_FRAGMENTS):
            return None
    return event


def init_sentry(component: str) -> bool:
    """Initialize Sentry for a given component ('api' or 'worker').

    Returns True if initialized, False if skipped (no DSN or already initialized).
    """
    settings = get_settings()
    if not settings.sentry_dsn:
        log.info("sentry_skipped_no_dsn", component=component)
        return False

    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=settings.env,
        release="social_inbox@0.1.0",
        traces_sample_rate=0.1,
        profiles_sample_rate=0.0,
        send_default_pii=False,
        before_send=_before_send,  # type: ignore[arg-type]
        integrations=[
            FastApiIntegration(transaction_style="endpoint"),
            StarletteIntegration(transaction_style="endpoint"),
            AsyncioIntegration(),
        ],
    )
    sentry_sdk.set_tag("component", component)
    log.info("sentry_initialized", component=component, env=settings.env)
    return True
