#!/usr/bin/env bash
# Daily Postgres backup for social_inbox.
#
# Strategy: pg_dump to local /var/backups/social_inbox/, then sync newest
# to offsite (rclone). Retention: 14 daily + 4 weekly (Sundays).
#
# Run via cron: see crontab.example
# Manual run: ./deploy/backup/backup.sh

set -euo pipefail

# --- Config (override via env if needed) ---
BACKUP_DIR="${BACKUP_DIR:-/var/backups/social_inbox}"
RCLONE_REMOTE="${RCLONE_REMOTE:-}"
RETAIN_DAILY="${RETAIN_DAILY:-14}"
RETAIN_WEEKLY="${RETAIN_WEEKLY:-4}"

PROJECT_DIR="${PROJECT_DIR:-/opt/social_inbox}"

# --- Setup ---
mkdir -p "$BACKUP_DIR/daily" "$BACKUP_DIR/weekly"
DATE=$(date -u +%Y-%m-%d)
DAY_OF_WEEK=$(date -u +%u)

DAILY_FILE="$BACKUP_DIR/daily/social_inbox-$DATE.sql.gz"
WEEKLY_FILE="$BACKUP_DIR/weekly/social_inbox-$DATE.sql.gz"

# --- Dump ---
cd "$PROJECT_DIR"

DB_USER=$(grep -E '^POSTGRES_USER=' .env.compose | cut -d= -f2-)
DB_NAME=$(grep -E '^POSTGRES_DB=' .env.compose | cut -d= -f2-)

if [ -z "$DB_USER" ] || [ -z "$DB_NAME" ]; then
    echo "ERROR: POSTGRES_USER or POSTGRES_DB missing from .env.compose"
    exit 1
fi

echo "[$(date -u +%H:%M:%S)] pg_dump -> $DAILY_FILE"
docker compose exec -T postgres pg_dump \
    -U "$DB_USER" \
    -d "$DB_NAME" \
    --no-owner --clean --if-exists \
    | gzip > "$DAILY_FILE"

SIZE=$(stat -c%s "$DAILY_FILE")
echo "[$(date -u +%H:%M:%S)] dump size: $SIZE bytes"

# Sanity: refuse to keep dumps smaller than 1KB (likely empty/failed)
if [ "$SIZE" -lt 1024 ]; then
    echo "ERROR: dump too small, removing and exiting"
    rm "$DAILY_FILE"
    exit 1
fi

# Weekly snapshot on Sundays
if [ "$DAY_OF_WEEK" = "7" ]; then
    cp "$DAILY_FILE" "$WEEKLY_FILE"
    echo "[$(date -u +%H:%M:%S)] weekly snapshot: $WEEKLY_FILE"
fi

# --- Retention ---
find "$BACKUP_DIR/daily" -name "*.sql.gz" -type f -mtime "+$RETAIN_DAILY" -delete
find "$BACKUP_DIR/weekly" -name "*.sql.gz" -type f -mtime "+$((RETAIN_WEEKLY * 7))" -delete

# --- Offsite (optional) ---
if [ -n "$RCLONE_REMOTE" ]; then
    echo "[$(date -u +%H:%M:%S)] rclone sync to $RCLONE_REMOTE"
    rclone copy "$BACKUP_DIR" "$RCLONE_REMOTE/social_inbox/" \
        --include "*.sql.gz" \
        --max-age "${RETAIN_DAILY}d"
fi

echo "[$(date -u +%H:%M:%S)] backup complete"
