#!/usr/bin/env bash
# Verify .env has all required keys before deploy.

set -euo pipefail

cd "$(dirname "$0")/../.."

REQUIRED_KEYS=(
    "ENV"
    "POSTGRES_DSN"
    "POSTGRES_USER"
    "POSTGRES_PASSWORD"
    "POSTGRES_DB"
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
    "PUBLIC_HOST_INBOX"
    "PUBLIC_HOST_ADMIN"
    "TRAEFIK_ACME_EMAIL"
    "SENTRY_DSN"
)

if [ ! -f .env ]; then
    echo "FAIL: .env not found"
    exit 1
fi

MISSING=()
EMPTY=()
for key in "${REQUIRED_KEYS[@]}"; do
    if ! grep -q "^${key}=" .env; then
        MISSING+=("$key")
        continue
    fi
    value=$(grep "^${key}=" .env | cut -d= -f2-)
    if [ -z "$value" ]; then
        EMPTY+=("$key")
    fi
done

if [ ${#MISSING[@]} -gt 0 ]; then
    echo "FAIL: missing keys in .env: ${MISSING[*]}"
    exit 1
fi

if [ ${#EMPTY[@]} -gt 0 ]; then
    # SENTRY_DSN can be empty in dev; in prod it's strongly recommended
    echo "WARN: empty values for: ${EMPTY[*]}"
fi

echo "OK: all required keys present"
