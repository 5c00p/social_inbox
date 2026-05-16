#!/usr/bin/env bash
# Verify .env and .env.compose have all required keys before deploy.
#
# .env       → loaded into app containers (validated by pydantic Settings, extra=forbid).
# .env.compose → compose-level ${VAR} substitution (POSTGRES_*, PUBLIC_HOST_*, TRAEFIK_*).

set -euo pipefail

cd "$(dirname "$0")/../.."

# Keys expected in .env (declared in app/config.py:Settings).
APP_KEYS=(
    "ENV"
    "POSTGRES_DSN"
    "REDIS_URL"
    "MESSAGING_PROVIDER"
    "SENDPULSE_CLIENT_ID"
    "SENDPULSE_CLIENT_SECRET"
    "SENDPULSE_WEBHOOK_SECRET"
    "ANTHROPIC_API_KEY"
    "INTERNAL_API_TOKEN"
    "ADMIN_BASIC_AUTH_USER"
    "ADMIN_BASIC_AUTH_PASSWORD"
    "NOTIFICATION_BOT_TOKEN"
    "NOTIFICATION_ADMIN_CHAT_ID"
    "TELEGRAM_BOT_USERNAME"
    "PUBLIC_BASE_URL"
    "SENTRY_DSN"
)

# Keys expected in .env.compose (compose substitution only — NOT into app).
COMPOSE_KEYS=(
    "POSTGRES_USER"
    "POSTGRES_PASSWORD"
    "POSTGRES_DB"
    "PUBLIC_HOST_INBOX"
    "PUBLIC_HOST_ADMIN"
    "TRAEFIK_ACME_EMAIL"
)

fail=0

check_file() {
    local file="$1"
    shift
    local required_keys=("$@")

    if [ ! -f "$file" ]; then
        echo "FAIL: $file not found"
        fail=1
        return
    fi

    local missing=()
    local empty=()
    for key in "${required_keys[@]}"; do
        if ! grep -q "^${key}=" "$file"; then
            missing+=("$key")
            continue
        fi
        local value
        value=$(grep "^${key}=" "$file" | cut -d= -f2-)
        if [ -z "$value" ]; then
            empty+=("$key")
        fi
    done

    if [ ${#missing[@]} -gt 0 ]; then
        echo "FAIL: missing keys in $file: ${missing[*]}"
        fail=1
    fi
    if [ ${#empty[@]} -gt 0 ]; then
        # SENTRY_DSN can be empty in dev; in prod it's strongly recommended
        echo "WARN: empty values in $file: ${empty[*]}"
    fi
}

check_file ".env" "${APP_KEYS[@]}"
check_file ".env.compose" "${COMPOSE_KEYS[@]}"

if [ "$fail" -ne 0 ]; then
    exit 1
fi

echo "OK: .env + .env.compose have all required keys"
