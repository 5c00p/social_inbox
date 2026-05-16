# Go-Live Runbook (для Виктора)

Технический deep-dive по запуску. Юлин чек-лист — в `docs/Go_Live_Checklist.docx`.

## За 3 дня до запуска (T-3)

### Pre-flight checks

```bash
ssh deploy@vps
cd /opt/social_inbox
./deploy/scripts/env_check.sh                # все ключи на месте
docker compose --env-file .env.compose \
    -f docker-compose.yml -f deploy/docker-compose.prod.yml ps
./deploy/scripts/smoke_check.sh              # smoke OK
```

### Verify backups work

```bash
./deploy/backup/backup.sh                    # запусти руками
ls -lh /var/backups/social_inbox/daily/      # появился файл
gunzip -c /var/backups/social_inbox/daily/*.sql.gz | head -50  # читается
```

### Verify offsite backup (если настроено)

```bash
rclone ls $RCLONE_REMOTE/social_inbox/daily/ | tail -3
```

### Verify Telegram notifications

```bash
docker compose exec api python -c "
import asyncio
from app.services.notifications import notify_admin
asyncio.run(notify_admin('Test from runbook T-3'))
"
```

Юля должна получить сообщение в течение 30 секунд.

### Verify Sentry

```bash
docker compose exec api python -c "
import sentry_sdk
sentry_sdk.capture_message('Smoke test from T-3 runbook', level='info')
"
```

Должно появиться в Sentry dashboard в течение 2 минут.

## За 1 день (T-1)

### Smoke test from external network

С локальной машины (НЕ с VPS, чтобы пройти через CDN/DNS):

```bash
INTERNAL_API_TOKEN=<from-vps-.env> \
uv run python scripts/smoke_test.py \
    --base-url https://inbox.your-domain.com \
    --admin-url https://inbox-admin.your-domain.com \
    --all
```

Все шаги должны быть `[OK]`.

### Verify bot_purify integration

С VPS:

```bash
docker compose -f /opt/bot_purify/docker-compose.yml exec bot python -c "
import asyncio
from bot.services.social_inbox import fetch_lead
result = asyncio.run(fetch_lead('test_nonexistent'))
print('Result:', result)  # expected: None
"
```

Если получаешь `None` — связь работает. Если `ConnectionError` — проверь networks.

### Cron alive

```bash
crontab -l | grep backup.sh    # есть строка
sudo tail -20 /var/log/social_inbox_backup.log  # есть свежие записи
```

## День запуска (T-0)

### Step 1: Final smoke

```bash
cd /opt/social_inbox
./deploy/scripts/smoke_check.sh
```

### Step 2: Canary keyword

В админке (Юля делает):
- Открыть Ключевые слова → найти keyword "очищение"
- Убедиться что priority=50, scenario=default_purify_comment, context=comment
- НЕ создавать новые keywords пока

В SendPulse:
- Webhook URL: `https://inbox.your-domain.com/webhooks/sendpulse`
- События: messages + comments

### Step 3: One Reels test

Юля публикует **один** Reels с CTA «Напиши ОЧИЩЕНИЕ в комментариях».

Виктор наблюдает в логах:

```bash
docker compose logs -f --tail 100 api worker | grep -E "(webhook|event_processing|scenario|outgoing)"
```

### Step 4: Verify first lead

Кто-то комментирует «ОЧИЩЕНИЕ» под Reels:

1. В логах появляется `event_processing event_type=comment`
2. В логах: `scenario_dispatch scenario_type=comment_to_dm`
3. В логах: `outgoing_sent send_ok=true`
4. В БД:
   ```bash
   docker compose exec postgres psql -U social_inbox -c \
       "SELECT id, username, short_id FROM social_users ORDER BY id DESC LIMIT 1;"
   ```
5. Тот же человек получает DM в Instagram (Юля проверяет)
6. Нажав на кнопку «Перейти в Telegram» — попадает в bot_purify
7. В БД:
   ```bash
   docker compose exec postgres psql -U social_inbox -c \
       "SELECT tg_user_id, tg_handover_at FROM social_users WHERE username = '<тот_username>';"
   ```
   Должны быть заполнены.

### Step 5: Observe 24 hours

- Юля: смотрит handover-уведомления в Telegram
- Юля: открывает админку, отвечает на handover диалоги
- Виктор: смотрит логи на ошибки
- Виктор: проверяет daily_digest утром следующего дня

Если за 24 часа без серьёзных проблем — расширяем:
- Добавляем keyword под другими Reels
- Подключаем DM welcome (он уже работает, просто верифицируем)

## Rollback

### Code rollback

```bash
cd /opt/social_inbox
git log --oneline -10
git reset --hard <previous-commit-sha>
./deploy/scripts/deploy.sh
```

### Disable acquisition completely

В админке: открыть Ключевые слова → деактивировать все keywords типа `comment_to_dm`.

Или быстрее — отключить webhook в SendPulse (там одна кнопка).

### Disable Claude smart-replies

В админке: Сценарии → `default_smart` → снять галочку Активен.

Engine сразу падает в fallback (echo), пользователи получают «Получено: ...» вместо умных ответов. Не идеально, но безопасно.

### Disable AI for a specific abusive user

В админке: открыть диалог → переключатель «AI режим включён» в выключенный.

### Full stop

```bash
docker compose --env-file .env.compose \
    -f docker-compose.yml -f deploy/docker-compose.prod.yml stop
```

Сервис лежит, webhook'и от SendPulse возвращают connection refused (SendPulse retry'нет, потом перестанет). Данные в БД сохранены. Запустить обратно: `... up -d`.

## Common issues during first week

### Claude отвечает странно

- Проверь `messages` таблицу: `SELECT text, safety_blocked FROM messages WHERE direction='out' ORDER BY created_at DESC LIMIT 20`
- Если много `safety_blocked=TRUE` — улучшить system prompt в `app/prompts/system_smart.md`
- Если ответы слишком общие — увеличить max_tokens с 500 до 800 в `app/services/claude_responder.py`

### Юля жалуется на спам уведомлений

- Проверь `app/observability/alerts.py` — увеличить TTL для dedup
- Проверь дублирующиеся handover триггеры в `tasks_watchdog.py`

### Conversion rate низкий

- Проверь deep-link в outgoing messages: `SELECT text FROM messages WHERE direction='out' AND created_at > NOW() - INTERVAL '1 day';`
- Проверь bot_purify логи: `docker compose -f /opt/bot_purify/docker-compose.yml logs --tail 100`
- Если bot_purify падает на нашем API — проверь internal network

### Disk filling up

```bash
df -h
du -sh /var/lib/docker /var/backups/social_inbox /var/log
```

Если docker volumes пухнут — Postgres логи. Включить log rotation в postgresql.conf или просто рестартнуть.
