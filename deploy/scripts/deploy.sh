#!/usr/bin/env bash
# Pull latest code and restart the production stack.
#
# Usage:
#   ssh deploy@vps
#   cd /opt/social_inbox
#   ./deploy/scripts/deploy.sh

set -euo pipefail

cd "$(dirname "$0")/../.."

COMPOSE_FILES=(-f docker-compose.yml -f deploy/docker-compose.prod.yml)

echo "[$(date -u +%H:%M:%S)] env_check..."
./deploy/scripts/env_check.sh

echo "[$(date -u +%H:%M:%S)] git pull..."
git pull --ff-only

echo "[$(date -u +%H:%M:%S)] building images..."
docker compose "${COMPOSE_FILES[@]}" build

echo "[$(date -u +%H:%M:%S)] applying changes..."
docker compose "${COMPOSE_FILES[@]}" up -d

echo "[$(date -u +%H:%M:%S)] waiting for healthchecks (30s)..."
sleep 30

./deploy/scripts/smoke_check.sh

echo "[$(date -u +%H:%M:%S)] deploy complete"
