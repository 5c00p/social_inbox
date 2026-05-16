# Task 17: Production deployment

> Применить в `D:\Work\social_inbox` после Tasks 01, 03, 04, 06, 07, 08, 09, 11, 13, 14, 15, 16, **и после Task 05 (SendPulseProvider) — без него развёртывать смысла нет**.
>
> Положить файл в корень проекта, открыть в VS Code, попросить Claude Code: «Прочитай TASK_17_production_deployment.md и выполни все подзадачи последовательно. Перед каждой подзадачей кратко опиши план, после — что фактически сделано».
>
> **Внимание:** часть подзадач — это команды на самом VPS, а не код. Claude Code их не выполнит, выполнит Виктор руками после генерации файлов.

---

## Контекст

После Tasks 01–16 у нас полностью функциональный социальный inbox с:
- Webhook handler, worker, scenarios, Claude, safety, admin, monitoring
- Тестами e2e через FakeProvider — всё проверено локально

Что осталось — **развернуть это в проде**. Цели:
1. Сервис доступен по HTTPS через `inbox.<domain>` (для SendPulse webhooks)
2. Юля заходит в админку через `inbox-admin.<domain>`
3. bot_purify обращается к `/api/lead/{short_id}` через internal docker network
4. Postgres бэкапится ежедневно с offsite копией
5. Логи ротируются автоматически
6. Перезапуск compose не теряет данных (volumes сохраняются)
7. Сертификаты Let's Encrypt автоматически продлеваются

**Архитектура деплоя:**

```
                          Internet
                              │
                              ▼
                  ┌───────────────────────┐
                  │   Traefik (80/443)    │
                  │   - HTTPS, ACME       │
                  │   - service discovery │
                  └─────┬──────────┬──────┘
                        │          │
        inbox.domain    │          │ inbox-admin.domain
                        │          │
                        ▼          ▼
              ┌──────────┐    ┌──────────┐
              │   api    │    │  admin   │
              │  :8000   │    │  :8501   │
              └────┬─────┘    └─────┬────┘
                   │                │
              ┌────┴─────┐          │
              │ worker   │          │
              └────┬─────┘          │
                   │                │
        ┌──────────┴──────┬─────────┘
        ▼                 ▼
    ┌────────┐       ┌────────┐
    │postgres│       │ redis  │
    └────────┘       └────────┘
        │
        │  ←──── internal docker network
        │        (purify-shared)
        ▼
   ┌──────────────┐
   │  bot_purify  │  ←── deployed separately
   │   (existing) │      uses http://social-inbox-api:8000
   └──────────────┘

```

---

## Цель

После выполнения этой задачи:

- На VPS Виктора развёрнут `social_inbox` со всеми сервисами
- Traefik с автоматическим Let's Encrypt сертификатом
- HTTPS на `inbox.<domain>` (webhook) и `inbox-admin.<domain>` (админка)
- bot_purify в отдельном compose может обращаться к social_inbox по docker network
- Ежедневный pg_dump с retention 14 daily + 4 weekly + offsite копия
- Docker logging rotation настроен (max 10MB × 5 файлов на контейнер)
- Production `.env` с реальными credentials лежит на VPS с правами 600
- Команды управления документированы в `deploy/README.md` (runbook)
- Smoke-проверки после деплоя проходят
- Если возникнет downtime — корректное восстановление по runbook

---

## Подзадачи

### 1. Структура deploy/

a) Создать каталог `deploy/` в корне проекта:

```
deploy/
├── README.md                       # runbook: deploy / restart / rollback / backup
├── docker-compose.prod.yml         # production overrides (Traefik labels, no volumes-as-code)
├── docker-compose.bot-purify.yml   # example for bot_purify integration with shared network
├── traefik/
│   ├── traefik.yml                 # main Traefik config
│   └── acme.json.example           # placeholder; real file created on first run
├── backup/
│   ├── backup.sh                   # daily pg_dump + retention + offsite
│   ├── restore.sh                  # restore from a dump (manual operation)
│   └── crontab.example             # cron schedule
└── scripts/
    ├── deploy.sh                   # git pull + docker compose up
    ├── smoke_check.sh              # post-deploy verification
    └── env_check.sh                # validate .env has all required keys
```

### 2. Production compose

a) Создать `deploy/docker-compose.prod.yml`:

```yaml
# Production overrides for social_inbox.
#
# Usage:
#   docker compose -f docker-compose.yml -f deploy/docker-compose.prod.yml up -d
#
# Differences from base docker-compose.yml:
# - No source mount volumes (containers run baked image)
# - Traefik labels for routing
# - Logging rotation
# - Connected to external 'purify-shared' network for bot_purify access
# - Postgres volume named explicitly for backup script compatibility
# - No exposed ports for api/admin/postgres/redis (only via Traefik)

services:
  api:
    restart: always
    volumes: []  # override base mount of ./app
    ports: []    # no direct port exposure; only via Traefik
    logging:
      driver: json-file
      options:
        max-size: "10m"
        max-file: "5"
    labels:
      - "traefik.enable=true"
      - "traefik.http.routers.api.rule=Host(`${PUBLIC_HOST_INBOX}`)"
      - "traefik.http.routers.api.entrypoints=websecure"
      - "traefik.http.routers.api.tls.certresolver=letsencrypt"
      - "traefik.http.services.api.loadbalancer.server.port=8000"

  worker:
    restart: always
    volumes: []
    logging:
      driver: json-file
      options:
        max-size: "10m"
        max-file: "5"
    labels:
      - "traefik.enable=false"

  admin:
    restart: always
    volumes: []
    ports: []
    logging:
      driver: json-file
      options:
        max-size: "10m"
        max-file: "5"
    labels:
      - "traefik.enable=true"
      - "traefik.http.routers.admin.rule=Host(`${PUBLIC_HOST_ADMIN}`)"
      - "traefik.http.routers.admin.entrypoints=websecure"
      - "traefik.http.routers.admin.tls.certresolver=letsencrypt"
      - "traefik.http.services.admin.loadbalancer.server.port=8501"

  postgres:
    restart: always
    ports: []
    logging:
      driver: json-file
      options:
        max-size: "10m"
        max-file: "3"

  redis:
    restart: always
    ports: []
    logging:
      driver: json-file
      options:
        max-size: "10m"
        max-file: "3"

  traefik:
    image: traefik:v3.2
    restart: always
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./deploy/traefik/traefik.yml:/etc/traefik/traefik.yml:ro
      - ./deploy/traefik/acme.json:/acme.json
      - /var/run/docker.sock:/var/run/docker.sock:ro
    logging:
      driver: json-file
      options:
        max-size: "10m"
        max-file: "3"
    networks:
      - default

networks:
  default:
    name: purify-shared
    external: true
```

   **Важное:** базовый `docker-compose.yml` мы НЕ редактируем — он остаётся для локальной разработки. Production использует overlay.

### 3. Traefik config

a) Создать `deploy/traefik/traefik.yml`:

```yaml
# Traefik static configuration.
# Dynamic config (routes/services) comes from docker labels.

global:
  checkNewVersion: false
  sendAnonymousUsage: false

api:
  dashboard: false  # do not expose Traefik dashboard publicly
  insecure: false

entryPoints:
  web:
    address: ":80"
    # Redirect HTTP → HTTPS for all hosts
    http:
      redirections:
        entryPoint:
          to: websecure
          scheme: https
          permanent: true
  websecure:
    address: ":443"

providers:
  docker:
    endpoint: "unix:///var/run/docker.sock"
    exposedByDefault: false  # opt-in via traefik.enable=true label
    network: purify-shared

certificatesResolvers:
  letsencrypt:
    acme:
      email: ${TRAEFIK_ACME_EMAIL}
      storage: /acme.json
      tlsChallenge: {}

log:
  level: INFO
  format: json

accessLog:
  format: json
  filters:
    statusCodes:
      - "400-599"  # log only errors to save disk
```

b) Создать `deploy/traefik/acme.json.example` (пустой плейсхолдер):

   В файле напишите одну строку-комментарий:

   ```
   # This file is auto-created by Traefik on first start.
   # On the VPS: touch acme.json && chmod 600 acme.json BEFORE first compose up.
   ```

   **Зачем:** Traefik требует acme.json с правами 600. Если файл создастся через docker volume mount без правильных прав — Let's Encrypt не сработает.

### 4. Bot_purify integration compose example

a) Создать `deploy/docker-compose.bot-purify.yml`:

```yaml
# Example overlay for bot_purify project to connect to social_inbox network.
#
# Place this file in D:\Work\bot_purify\ directory.
# Use as:
#   docker compose -f docker-compose.yml -f docker-compose.bot-purify.yml up -d
#
# This DOES NOT replace bot_purify's own compose; it adds the shared network
# so bot_purify can reach social_inbox via http://social-inbox-api:8000

services:
  bot:
    networks:
      - default
      - purify-shared

networks:
  purify-shared:
    name: purify-shared
    external: true
```

   **Замечание:** имя сервиса в social_inbox compose — `api`. Но docker compose даёт алиасы по project name + service name. На уровне docker network, имя контейнера = `social-inbox-api-1` (с дефисами) или похожее. Чтобы bot_purify обращался по `http://social-inbox-api:8000`, нужно дать api контейнеру **alias** в shared сети. Это сделано через `container_name` или `networks.aliases`.

   **Уточнение к подзадаче 2** — в `deploy/docker-compose.prod.yml` в сервис `api` добавить:

```yaml
  api:
    # ...existing...
    networks:
      default:
        aliases:
          - social-inbox-api
```

   Аналогично для других сервисов если потребуется кросс-доступ. Сейчас bot_purify обращается только к api.

### 5. Backup scripts

a) Создать `deploy/backup/backup.sh`:

```bash
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
RCLONE_REMOTE="${RCLONE_REMOTE:-}"  # e.g. "b2:social-inbox-backups" — leave empty to skip offsite
RETAIN_DAILY="${RETAIN_DAILY:-14}"
RETAIN_WEEKLY="${RETAIN_WEEKLY:-4}"

# Project directory (where docker compose lives)
PROJECT_DIR="${PROJECT_DIR:-/opt/social_inbox}"

# --- Setup ---
mkdir -p "$BACKUP_DIR/daily" "$BACKUP_DIR/weekly"
DATE=$(date -u +%Y-%m-%d)
DAY_OF_WEEK=$(date -u +%u)  # 7 = Sunday

DAILY_FILE="$BACKUP_DIR/daily/social_inbox-$DATE.sql.gz"
WEEKLY_FILE="$BACKUP_DIR/weekly/social_inbox-$DATE.sql.gz"

# --- Dump ---
cd "$PROJECT_DIR"

echo "[$(date -u +%H:%M:%S)] pg_dump → $DAILY_FILE"
docker compose exec -T postgres pg_dump \
    -U "$(grep -E '^POSTGRES_USER=' .env | cut -d= -f2)" \
    -d "$(grep -E '^POSTGRES_DB=' .env | cut -d= -f2)" \
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
```

b) Сделать исполняемым: в подзадачу 11 добавить `chmod +x deploy/backup/backup.sh`.

c) Создать `deploy/backup/restore.sh`:

```bash
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

read -p "This will DROP and recreate the database. Type 'yes' to confirm: " CONFIRM
if [ "$CONFIRM" != "yes" ]; then
    echo "Cancelled."
    exit 0
fi

echo "Stopping api and worker (preserve postgres + redis running)..."
docker compose stop api worker admin

DB_USER=$(grep -E '^POSTGRES_USER=' .env | cut -d= -f2)
DB_NAME=$(grep -E '^POSTGRES_DB=' .env | cut -d= -f2)

echo "Restoring from $DUMP_FILE..."
gunzip -c "$DUMP_FILE" | docker compose exec -T postgres psql \
    -U "$DB_USER" -d "$DB_NAME"

echo "Restarting services..."
docker compose start api worker admin

echo "Restore complete. Verify via curl https://${PUBLIC_HOST_INBOX}/ready"
```

d) Создать `deploy/backup/crontab.example`:

```cron
# social_inbox backup schedule.
# Install: sudo crontab -u <deploy_user> -e   then paste these lines.

# Daily backup at 03:00 UTC (off-hours for IG audience in EU/Asia)
0 3 * * * /opt/social_inbox/deploy/backup/backup.sh >> /var/log/social_inbox_backup.log 2>&1
```

### 6. Deploy / smoke scripts

a) Создать `deploy/scripts/deploy.sh`:

```bash
#!/usr/bin/env bash
# Pull latest code and restart the production stack.
#
# Usage:
#   ssh deploy@vps
#   cd /opt/social_inbox
#   ./deploy/scripts/deploy.sh

set -euo pipefail

cd "$(dirname "$0")/../.."

echo "[$(date -u +%H:%M:%S)] git pull..."
git pull --ff-only

echo "[$(date -u +%H:%M:%S)] building images..."
docker compose -f docker-compose.yml -f deploy/docker-compose.prod.yml build

echo "[$(date -u +%H:%M:%S)] applying changes..."
docker compose -f docker-compose.yml -f deploy/docker-compose.prod.yml up -d

echo "[$(date -u +%H:%M:%S)] waiting for healthchecks (30s)..."
sleep 30

./deploy/scripts/smoke_check.sh

echo "[$(date -u +%H:%M:%S)] deploy complete"
```

b) Создать `deploy/scripts/smoke_check.sh`:

```bash
#!/usr/bin/env bash
# Post-deploy smoke checks.
# Runs from VPS, uses public URLs.

set -euo pipefail

cd "$(dirname "$0")/../.."

# Load env so we can reference PUBLIC_HOST_*
set -a
source .env
set +a

PUBLIC_INBOX="https://${PUBLIC_HOST_INBOX}"
PUBLIC_ADMIN="https://${PUBLIC_HOST_ADMIN}"

echo "Checking $PUBLIC_INBOX/health..."
HTTP=$(curl -sS -o /dev/null -w "%{http_code}" "$PUBLIC_INBOX/health" || echo "000")
if [ "$HTTP" != "200" ]; then
    echo "FAIL: /health returned $HTTP"
    exit 1
fi

echo "Checking $PUBLIC_INBOX/ready/quick..."
HTTP=$(curl -sS -o /dev/null -w "%{http_code}" "$PUBLIC_INBOX/ready/quick" || echo "000")
if [ "$HTTP" != "200" ]; then
    echo "FAIL: /ready/quick returned $HTTP"
    exit 1
fi

echo "Checking $PUBLIC_INBOX/ready (full)..."
HTTP=$(curl -sS -o /dev/null -w "%{http_code}" "$PUBLIC_INBOX/ready" || echo "000")
if [ "$HTTP" != "200" ]; then
    echo "WARN: /ready returned $HTTP (worker heartbeat may not be ready yet)"
fi

echo "Checking $PUBLIC_ADMIN..."
HTTP=$(curl -sS -o /dev/null -w "%{http_code}" -L "$PUBLIC_ADMIN" || echo "000")
if [ "$HTTP" -lt 200 ] || [ "$HTTP" -ge 500 ]; then
    echo "FAIL: admin dashboard returned $HTTP"
    exit 1
fi

echo "Checking webhook GET (verification challenge)..."
RESP=$(curl -sS "$PUBLIC_INBOX/webhooks/sendpulse?hub.challenge=test123" || echo "")
if ! echo "$RESP" | grep -q "test123"; then
    echo "WARN: webhook verification did not echo challenge: $RESP"
fi

echo "All smoke checks passed."
```

c) Создать `deploy/scripts/env_check.sh`:

```bash
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
```

### 7. Production .env template

a) Создать `deploy/.env.prod.example`:

```bash
# --- Production environment variables ---
# Copy to /opt/social_inbox/.env on VPS and fill in real values.
# Then: chmod 600 .env && chown <deploy_user>:<deploy_user> .env

# --- Environment ---
ENV=prod
LOG_LEVEL=INFO

# --- Database ---
POSTGRES_USER=social_inbox
POSTGRES_PASSWORD=<generate-strong-password>
POSTGRES_DB=social_inbox
POSTGRES_DSN=postgresql://social_inbox:<same-password>@postgres:5432/social_inbox

# --- Redis ---
REDIS_URL=redis://redis:6379/0

# --- Messaging provider ---
MESSAGING_PROVIDER=sendpulse
SENDPULSE_CLIENT_ID=<from-yulia>
SENDPULSE_CLIENT_SECRET=<from-yulia>
SENDPULSE_WEBHOOK_SECRET=<from-yulia>

# --- Anthropic ---
ANTHROPIC_API_KEY=<from-console.anthropic.com>
CLAUDE_DEFAULT_MODEL=claude-sonnet-4-6

# --- Internal API (shared with bot_purify) ---
# Same value MUST be set in bot_purify's .env as SOCIAL_INBOX_API_TOKEN
INTERNAL_API_TOKEN=<generate-long-random-string>

# --- Admin dashboard ---
ADMIN_BASIC_AUTH_USER=yulia
ADMIN_BASIC_AUTH_PASSWORD=<generate-strong-password>

# --- Notification bot (Telegram) ---
NOTIFICATION_BOT_TOKEN=<from-BotFather>
NOTIFICATION_ADMIN_CHAT_ID=<numeric-chat-id>

# --- Telegram bot username (no @, no t.me/) ---
TELEGRAM_BOT_USERNAME=yuliya_purify_bot

# --- Public URLs ---
PUBLIC_BASE_URL=https://inbox.your-domain.com
PUBLIC_HOST_INBOX=inbox.your-domain.com
PUBLIC_HOST_ADMIN=inbox-admin.your-domain.com
TRAEFIK_ACME_EMAIL=victor@your-domain.com

# --- Monitoring ---
SENTRY_DSN=https://...@sentry.io/...
```

### 8. Документация — runbook

a) Создать `deploy/README.md`:

````markdown
# social_inbox — deployment runbook

This document describes how to deploy, restart, back up, and recover the
social_inbox stack on the production VPS.

## One-time setup (per VPS)

### 1. Prerequisites on VPS

- Ubuntu 22.04+ or Debian 12+
- Docker Engine + docker compose plugin
- Domain pointed to VPS IP:
  - `inbox.<domain>` — A record
  - `inbox-admin.<domain>` — A record
- Outbound 443 access (for Let's Encrypt, SendPulse, Anthropic, Telegram)

### 2. Clone repo

```bash
sudo mkdir -p /opt/social_inbox
sudo chown $USER:$USER /opt/social_inbox
cd /opt/social_inbox
git clone git@github.com:<owner>/social_inbox.git .
```

### 3. Configure environment

```bash
cp deploy/.env.prod.example .env
$EDITOR .env  # fill in real values
chmod 600 .env
./deploy/scripts/env_check.sh
```

### 4. External docker network

This network is shared with bot_purify so they can reach each other.

```bash
docker network create purify-shared
```

If bot_purify is already deployed and has its own network, recreate it with name `purify-shared`, or update bot_purify compose to join `purify-shared` as external.

### 5. Initialize ACME storage for Traefik

```bash
touch deploy/traefik/acme.json
chmod 600 deploy/traefik/acme.json
```

### 6. First deploy

```bash
docker compose -f docker-compose.yml -f deploy/docker-compose.prod.yml up -d
```

Watch logs for first 2-3 minutes:

```bash
docker compose logs -f traefik api
```

Traefik will request Let's Encrypt certificates on first HTTPS request. Wait for `Server responded with a certificate` in logs, then verify:

```bash
curl https://inbox.<domain>/health
```

### 7. Configure SendPulse webhook URL

In SendPulse dashboard → Chatbot → Webhooks, set:

- URL: `https://inbox.<domain>/webhooks/sendpulse`
- Events: messages + comments

### 8. Configure bot_purify

Edit `/opt/bot_purify/.env`:

```bash
SOCIAL_INBOX_API_URL=http://social-inbox-api:8000
SOCIAL_INBOX_API_TOKEN=<same-as-INTERNAL_API_TOKEN-from-social_inbox-.env>
```

Apply `deploy/docker-compose.bot-purify.yml` overlay to add `purify-shared` network:

```bash
cd /opt/bot_purify
docker compose -f docker-compose.yml -f /opt/social_inbox/deploy/docker-compose.bot-purify.yml up -d
```

### 9. Cron for backups

```bash
crontab -e
# Paste contents of deploy/backup/crontab.example
sudo mkdir -p /var/backups/social_inbox /var/log
sudo touch /var/log/social_inbox_backup.log
sudo chown $USER /var/log/social_inbox_backup.log /var/backups/social_inbox
```

### 10. (Optional) Offsite backup config

Install rclone, configure remote (e.g. Backblaze B2), then:

```bash
echo 'export RCLONE_REMOTE="b2:social-inbox-backups"' >> ~/.bashrc
source ~/.bashrc
```

## Routine operations

### Deploy a new version

```bash
cd /opt/social_inbox
./deploy/scripts/deploy.sh
```

### Restart all services

```bash
docker compose -f docker-compose.yml -f deploy/docker-compose.prod.yml restart
```

### Restart one service

```bash
docker compose -f docker-compose.yml -f deploy/docker-compose.prod.yml restart api
```

### View logs

```bash
docker compose -f docker-compose.yml -f deploy/docker-compose.prod.yml logs -f api
docker compose -f docker-compose.yml -f deploy/docker-compose.prod.yml logs --since 1h worker
```

### Connect to database

```bash
docker compose exec postgres psql -U social_inbox social_inbox
```

### Manual backup

```bash
./deploy/backup/backup.sh
```

### Restore from backup

```bash
ls /var/backups/social_inbox/daily/  # find the file
./deploy/backup/restore.sh /var/backups/social_inbox/daily/social_inbox-2026-05-07.sql.gz
```

## Troubleshooting

### Traefik can't get certificate

Check:
- DNS A records point to this VPS
- Ports 80 and 443 open (firewall, cloud provider security group)
- `acme.json` has 600 permissions
- `TRAEFIK_ACME_EMAIL` in `.env` is valid

Reset ACME state (use only if certs are completely broken):

```bash
docker compose stop traefik
rm deploy/traefik/acme.json
touch deploy/traefik/acme.json
chmod 600 deploy/traefik/acme.json
docker compose start traefik
```

### bot_purify can't reach social_inbox

Verify network membership:

```bash
docker network inspect purify-shared
```

Both `social-inbox-api` and `bot_purify-bot` (or similar) containers should be in `Containers` list.

Test from inside bot_purify container:

```bash
docker compose -f /opt/bot_purify/docker-compose.yml exec bot \
    curl -i -H "X-Internal-Token: $SOCIAL_INBOX_API_TOKEN" \
    http://social-inbox-api:8000/api/lead/nonexistent
```

Expected: 404 (lead not found), NOT connection error.

### Worker is not processing events

```bash
docker compose logs --tail 100 worker
docker compose exec redis redis-cli GET worker:heartbeat
docker compose exec redis redis-cli LLEN arq:queue
```

If queue is growing but worker shows no activity, restart worker:

```bash
docker compose restart worker
```

### Sentry / Telegram notifications

Verify env vars are present:

```bash
./deploy/scripts/env_check.sh
```

Test notification manually:

```bash
docker compose exec api python -c "
import asyncio
from app.services.notifications import notify_admin
asyncio.run(notify_admin('Test from VPS deploy'))
"
```

## Rollback

### Code rollback

```bash
cd /opt/social_inbox
git log --oneline -10  # find previous commit
git reset --hard <prev-commit-sha>
./deploy/scripts/deploy.sh
```

### Data rollback

Use `restore.sh` from the most recent good backup. See "Restore from backup" above.

## Security checklist

- [ ] `.env` has chmod 600
- [ ] `acme.json` has chmod 600
- [ ] Postgres port is NOT exposed publicly (only via docker network)
- [ ] Redis port is NOT exposed publicly
- [ ] Admin dashboard is behind Basic Auth (verified)
- [ ] HTTPS works on both domains (verified via `curl -I`)
- [ ] HTTP redirects to HTTPS (verified)
- [ ] SSH key authentication only (no password login)
- [ ] Automatic security updates enabled (`unattended-upgrades`)
- [ ] Backups have offsite copy (`rclone copy` runs successfully)
````

### 9. Поддержка sentry/healthcheck в Docker compose

a) Убедиться что в базовом `docker-compose.yml` healthcheck'и из Task 16 присутствуют. Если нет — добавить (см. Task 16 подзадача 9).

### 10. CLAUDE.md обновление

a) В CLAUDE.md в § 14 «Деплой и инфраструктура» добавить ссылку на runbook:

```markdown
### 14.7. Production runbook

Подробное руководство по развёртыванию, обновлению, бэкапам и восстановлению:
`deploy/README.md`.

Ключевые команды:
- Deploy новой версии: `cd /opt/social_inbox && ./deploy/scripts/deploy.sh`
- Smoke check: `./deploy/scripts/smoke_check.sh`
- Backup: `./deploy/backup/backup.sh`
- Restore: `./deploy/backup/restore.sh <dump.sql.gz>`
```

b) В § 17 (roadmap) пометить Task 17 как выполненный после применения.

### 11. Permissions на скрипты

a) После создания файлов всех скриптов выполнить:

```bash
chmod +x deploy/backup/backup.sh
chmod +x deploy/backup/restore.sh
chmod +x deploy/scripts/deploy.sh
chmod +x deploy/scripts/smoke_check.sh
chmod +x deploy/scripts/env_check.sh
```

   В git это сохранится через `git update-index --chmod=+x` если репо был склонирован под Windows.

---

## Acceptance criteria

Часть критериев — на VPS, не в локальной среде Claude Code.

**Локально (Claude Code может проверить):**

- [ ] Файлы созданы по структуре подзадач 1–7
- [ ] `deploy/README.md` существует и валидируется как markdown
- [ ] Bash-скрипты проходят `shellcheck` (если установлен) без критических предупреждений
- [ ] `docker compose -f docker-compose.yml -f deploy/docker-compose.prod.yml config` парсится без ошибок (валидация yaml)
- [ ] `make lint` и `make test` продолжают проходить
- [ ] CLAUDE.md обновлён в § 14.7 и § 17

**На VPS Виктора (вручную после деплоя):**

- [ ] DNS records настроены, `dig inbox.<domain>` возвращает IP VPS
- [ ] `./deploy/scripts/env_check.sh` показывает «OK: all required keys present»
- [ ] `docker compose ... up -d` стартует все 6 сервисов (api, worker, admin, postgres, redis, traefik)
- [ ] `curl https://inbox.<domain>/health` → 200 OK
- [ ] `curl https://inbox.<domain>/ready` → 200 после ~90 секунд (worker heartbeat)
- [ ] `curl -I https://inbox-admin.<domain>` → 200 после Basic Auth (или 401 без credentials)
- [ ] `curl http://inbox.<domain>/health` → 308 redirect на https://
- [ ] Traefik логи показывают «obtained certificate» для обоих доменов
- [ ] `acme.json` содержит реальный сертификат (не пустой)
- [ ] bot_purify в shared network: из его контейнера `curl http://social-inbox-api:8000/health` возвращает 200
- [ ] SendPulse webhook test (через UI SendPulse, отправить test event) — в логах api видна обработка
- [ ] Cron установлен: `crontab -l` показывает строку с `backup.sh`
- [ ] Ручной запуск `./deploy/backup/backup.sh` создаёт файл в `/var/backups/social_inbox/daily/`
- [ ] Daily digest приходит Юле утром в Telegram (можно подождать 24ч или запустить вручную)
- [ ] В Sentry появляется test exception если намеренно его кинуть из api

---

## Do NOT

- НЕ открывать порты Postgres (5432) или Redis (6379) наружу. Только internal docker network.
- НЕ открывать Traefik dashboard публично. `api.dashboard: false` в traefik.yml.
- НЕ хранить `.env` в git. Только `.env.prod.example` (шаблон без значений).
- НЕ давать `acme.json` права 644 или 755. Только 600, иначе Traefik откажется его использовать.
- НЕ запускать `git pull` без `--ff-only` в deploy.sh. Иначе при ручных правках на VPS будет automatic merge commit с потенциальными конфликтами.
- НЕ деплоить без Task 05 (SendPulseProvider). В проде нечего запускать — провайдер вернёт NotImplementedError на первом же webhook.
- НЕ забывать про timezone в crontab. cron на VPS обычно UTC; время `0 3 * * *` = 03:00 UTC.
- НЕ хранить бэкапы в S3 без шифрования. Они содержат PII (имена, тексты сообщений). Минимум — server-side encryption с KMS-ключом, лучше — клиентское шифрование через `gpg` перед загрузкой.
- НЕ использовать `:latest` тэги для docker images в проде. `postgres:16-alpine`, `redis:7-alpine`, `traefik:v3.2` — конкретные версии. Внезапный major upgrade — частая причина инцидентов.
- НЕ перезапускать `postgres` чаще чем нужно. Каждый рестарт — это потенциальный данных-fsync overhead. Только при апгрейде версии или изменении конфигурации.
- НЕ использовать root для запуска docker. Создай отдельного `deploy` пользователя в группе docker.
- НЕ давать `deploy/docker-compose.prod.yml` overrides на чтение исходного кода через volumes. В проде образ должен быть immutable.

---

## Зависимости задачи

- Все предыдущие Tasks применены: 01, 03, 04, 06, 07, 08, 09, 11, 13, 14, 15, 16, **05**
- VPS уже арендован, доступ по SSH настроен
- Домен куплен, DNS-провайдер позволяет настраивать A-records
- Email Виктора для Let's Encrypt уведомлений
- (Опционально) Аккаунт offsite-storage: B2/S3/Google Drive с rclone setup
- Юля передала SendPulse credentials (Task 05 заполнила их в реальный код)
- Anthropic API key (Виктор)
- Sentry проект создан (Task 16)
- Notification bot создан через @BotFather (Task 14)

---

## Что после этой задачи

После применения Task 17:

```
✅ social_inbox развёрнут на VPS с HTTPS
✅ Юля заходит в админку через inbox-admin.<domain>
✅ SendPulse шлёт webhooks на https://inbox.<domain>/webhooks/sendpulse
✅ bot_purify обращается к /api/lead/... через internal network
✅ Постгрес бэкапится ежедневно + offsite
✅ Логи ротируются
✅ Sentry собирает ошибки
✅ Telegram alerts работают
✅ Daily digest приходит Юле утром
```

**Это deployable state**. Можно показать Юле, она может пользоваться.

Осталась одна последняя задача:

- **Task 18** — Smoke tests + go-live checklist: формальная процедура проверки готовности перед запуском на реальную аудиторию

После Task 18 — **формально запущен**.

---

**Дата создания:** 2026-05-08
**Применять в:** `D:\Work\social_inbox` после всех остальных Tasks (особенно 05 и 16)
**Эстимейт:** 4–6 часов на Claude Code (генерация файлов) + 4–6 часов Виктора на VPS (manual setup)
