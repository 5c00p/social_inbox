"""Authentication helpers for internal API endpoints.

X-Internal-Token is a shared secret between social_inbox and bot_purify.
Uses constant-time comparison to prevent timing-based extraction.
"""

from __future__ import annotations

import hashlib
import secrets

from fastapi import Header, HTTPException, status

from app.config import get_settings


def verify_internal_token(
    x_internal_token: str | None = Header(default=None, alias="X-Internal-Token"),
) -> None:
    """FastAPI dependency: validate X-Internal-Token header.

    Returns nothing on success; raises 401 on failure.
    """
    settings = get_settings()
    expected = settings.internal_api_token

    if not x_internal_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing X-Internal-Token header",
        )

    if not secrets.compare_digest(x_internal_token, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid X-Internal-Token",
        )


def fingerprint_token(token: str) -> str:
    """Stable short fingerprint of a token, safe to use as Redis key.

    SHA-256 hex truncated to 16 chars. NOT for security — only for grouping
    requests by caller in rate-limit accounting.
    """
    return hashlib.sha256(token.encode()).hexdigest()[:16]
