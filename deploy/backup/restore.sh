#!/usr/bin/env bash
# Restore Postgres from a gzipped dump.
#
# DESTRUCTIVE: drops and recreates the database.
# Run manually only; not from cron.
#
# Usage:
#   ./deploy/backup/restore.sh /var/backups/social_inbox/daily/social_inbox-2026-05-08.sql.gz

set -euo pipefail

DUMP_FILE="${1:-}"
PROJECT_DIR="${PROJECT_DIR:-/opt/social_inbox}"

if [ -z "$DUMP_FILE" ]; then
    echo "Usage: $0 <path-to-dump.sql.gz>"
    exit 1
fi

if [ ! -f "$DUMP_FILE" ]; then
    echo "ERROR: file not found: $DUMP_FILE"
    exit 1
fi

cd "$PROJECT_DIR"

read -r -p "This will DROP and recreate the database. Type 'yes' to confirm: " CONFIRM
if [ "$CONFIRM" != "yes" ]; then
    echo "Cancelled."
    exit 0
fi

echo "Stopping api, worker, admin (postgres + redis stay running)..."
docker compose stop api worker admin

DB_USER=$(grep -E '^POSTGRES_USER=' .env | cut -d= -f2-)
DB_NAME=$(grep -E '^POSTGRES_DB=' .env | cut -d= -f2-)

if [ -z "$DB_USER" ] || [ -z "$DB_NAME" ]; then
    echo "ERROR: POSTGRES_USER or POSTGRES_DB missing from .env"
    exit 1
fi

echo "Restoring from $DUMP_FILE..."
gunzip -c "$DUMP_FILE" | docker compose exec -T postgres psql \
    -U "$DB_USER" -d "$DB_NAME"

echo "Restarting services..."
docker compose start api worker admin

PUBLIC_HOST_INBOX=$(grep -E '^PUBLIC_HOST_INBOX=' .env | cut -d= -f2-)
echo "Restore complete. Verify via: curl https://${PUBLIC_HOST_INBOX}/ready"
