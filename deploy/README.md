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

The stack uses **two** env files (kept separate to avoid pydantic `extra="forbid"`
rejecting compose-only vars in app containers):

- `.env` — app vars only (loaded into api/admin/worker via `env_file: .env`)
- `.env.compose` — compose substitution vars: `POSTGRES_USER/PASSWORD/DB`,
  `PUBLIC_HOST_INBOX/ADMIN`, `TRAEFIK_ACME_EMAIL`. Passed to compose via
  `--env-file .env.compose` (handled inside `deploy/scripts/deploy.sh`).

```bash
cp deploy/.env.prod.example .env
cp deploy/.env.compose.example .env.compose
$EDITOR .env          # fill in app credentials
$EDITOR .env.compose  # fill in Postgres password + public hosts + ACME email
chmod 600 .env .env.compose
./deploy/scripts/env_check.sh
```

The `POSTGRES_PASSWORD` in `.env.compose` MUST match the password embedded in
`POSTGRES_DSN` in `.env`.

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
docker compose --env-file .env.compose \
    -f docker-compose.yml -f deploy/docker-compose.prod.yml up -d
```

Watch logs for first 2-3 minutes:

```bash
docker compose --env-file .env.compose \
    -f docker-compose.yml -f deploy/docker-compose.prod.yml logs -f traefik api
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
sudo mkdir -p /var/backups/social_inbox /var/log
sudo touch /var/log/social_inbox_backup.log
sudo chown $USER /var/log/social_inbox_backup.log /var/backups/social_inbox

crontab -e
# Paste contents of deploy/backup/crontab.example
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
docker compose --env-file .env.compose -f docker-compose.yml -f deploy/docker-compose.prod.yml restart
```

### Restart one service

```bash
docker compose --env-file .env.compose -f docker-compose.yml -f deploy/docker-compose.prod.yml restart api
```

### View logs

```bash
docker compose --env-file .env.compose -f docker-compose.yml -f deploy/docker-compose.prod.yml logs -f api
docker compose --env-file .env.compose -f docker-compose.yml -f deploy/docker-compose.prod.yml logs --since 1h worker
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
docker compose --env-file .env.compose -f docker-compose.yml -f deploy/docker-compose.prod.yml stop traefik
rm deploy/traefik/acme.json
touch deploy/traefik/acme.json
chmod 600 deploy/traefik/acme.json
docker compose --env-file .env.compose -f docker-compose.yml -f deploy/docker-compose.prod.yml start traefik
```

### bot_purify can't reach social_inbox

Verify network membership:

```bash
docker network inspect purify-shared
```

Both `social-inbox-api` (alias) and `bot_purify-bot` (or similar) containers should be in the `Containers` list.

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
- [ ] `.env.compose` has chmod 600
- [ ] `acme.json` has chmod 600
- [ ] Postgres port is NOT exposed publicly (only via docker network)
- [ ] Redis port is NOT exposed publicly
- [ ] Admin dashboard is behind Basic Auth (verified)
- [ ] HTTPS works on both domains (verified via `curl -I`)
- [ ] HTTP redirects to HTTPS (verified)
- [ ] SSH key authentication only (no password login)
- [ ] Automatic security updates enabled (`unattended-upgrades`)
- [ ] Backups have offsite copy (`rclone copy` runs successfully)
