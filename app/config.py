"""Application configuration loaded from environment."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Centralised configuration. All env vars MUST be defined here, never read via os.getenv."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="forbid",
    )

    # --- Environment ---
    env: Literal["dev", "prod"] = "dev"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    # --- Database ---
    postgres_dsn: str = Field(..., description="postgresql://user:pass@host:port/db")

    # --- Redis ---
    redis_url: str = Field(..., description="redis://host:port/db")

    # --- Messaging provider (active) ---
    messaging_provider: Literal["sendpulse", "manychat", "meta"] = "sendpulse"

    # --- SendPulse credentials (used when messaging_provider='sendpulse') ---
    sendpulse_client_id: str = ""
    sendpulse_client_secret: str = ""
    sendpulse_webhook_secret: str = ""

    # --- Anthropic ---
    anthropic_api_key: str = ""
    claude_default_model: str = "claude-sonnet-4-6"

    # --- Internal API (shared with bot_purify) ---
    internal_api_token: str = Field(..., description="Shared secret with bot_purify")

    # --- Admin ---
    admin_basic_auth_user: str = "admin"
    admin_basic_auth_password: str = ""

    # --- Sentry ---
    sentry_dsn: str = ""

    # --- App URLs ---
    public_base_url: str = "http://localhost:8000"
    telegram_bot_username: str = "yuliya_purify_bot"

    # --- Notification bot (admin alerts to Yulia) ---
    notification_bot_token: str = ""
    notification_admin_chat_id: int = 0


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
