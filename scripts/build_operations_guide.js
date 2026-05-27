const { Document, Packer, Paragraph, TextRun,
        Header, Footer, AlignmentType, LevelFormat, HeadingLevel,
        BorderStyle, ShadingType, PageNumber, PageBreak } = require('docx');
const fs = require('fs');

const FONT = "Arial";
const MONO = "Consolas";
const ORANGE = "E86854";
const GREEN = "2E7D32";
const RED = "C62828";
const BLUE = "1565C0";
const GRAY = "888888";
const LIGHT_BG = "FFF4E6";
const CODE_BG = "F4F4F4";
const SUCCESS_BG = "E8F5E9";
const DANGER_BG = "FFEBEE";
const INFO_BG = "E3F2FD";

const P = (text, opts = {}) => new Paragraph({
  spacing: { after: 120 },
  ...opts,
  children: Array.isArray(text)
    ? text.map(t => t instanceof TextRun ? t : new TextRun({ ...t, font: FONT }))
    : [new TextRun({ text, font: FONT })],
});

const H1 = (text) => new Paragraph({
  heading: HeadingLevel.HEADING_1,
  spacing: { before: 360, after: 200 },
  children: [new TextRun({ text, bold: true, size: 32, font: FONT, color: ORANGE })],
});

const H2 = (text) => new Paragraph({
  heading: HeadingLevel.HEADING_2,
  spacing: { before: 280, after: 140 },
  children: [new TextRun({ text, bold: true, size: 26, font: FONT })],
});

const H3 = (text) => new Paragraph({
  heading: HeadingLevel.HEADING_3,
  spacing: { before: 200, after: 100 },
  children: [new TextRun({ text, bold: true, size: 22, font: FONT, color: BLUE })],
});

const Bullet = (parts, level = 0) => new Paragraph({
  numbering: { reference: "bullets", level },
  spacing: { after: 60 },
  children: parts.map(r => new TextRun({ ...r, font: FONT })),
});

const Code = (text) => {
  const lines = text.split('\n');
  return lines.map((line, idx) => new Paragraph({
    spacing: { after: idx === lines.length - 1 ? 120 : 0 },
    shading: { fill: CODE_BG, type: ShadingType.CLEAR },
    indent: { left: 200 },
    children: [new TextRun({ text: line || ' ', font: MONO, size: 20 })],
  }));
};

const Note = (parts, color = ORANGE, bg = LIGHT_BG) => new Paragraph({
  spacing: { before: 100, after: 100 },
  shading: { fill: bg, type: ShadingType.CLEAR },
  border: { left: { style: BorderStyle.SINGLE, size: 12, color, space: 8 } },
  indent: { left: 200 },
  children: parts.map(r => new TextRun({ ...r, font: FONT })),
});

const Warning = (parts) => Note([{ text: "[!] ", bold: true }, ...parts], RED, DANGER_BG);
const Info = (parts) => Note([{ text: "[i] ", bold: true }, ...parts], BLUE, INFO_BG);
const Success = (parts) => Note([{ text: "[OK] ", bold: true, color: GREEN }, ...parts], GREEN, SUCCESS_BG);

const HR = () => new Paragraph({
  spacing: { before: 200, after: 200 },
  border: { bottom: { style: BorderStyle.SINGLE, size: 6, color: ORANGE, space: 1 } },
  children: [new TextRun("")],
});

const children = [
  // Cover
  new Paragraph({
    alignment: AlignmentType.CENTER,
    spacing: { before: 1400, after: 200 },
    children: [new TextRun({ text: "social_inbox", bold: true, size: 56, font: FONT, color: ORANGE })],
  }),
  new Paragraph({
    alignment: AlignmentType.CENTER,
    spacing: { after: 400 },
    children: [new TextRun({ text: "Инструкция по эксплуатации", size: 32, font: FONT })],
  }),
  new Paragraph({
    alignment: AlignmentType.CENTER,
    spacing: { after: 200 },
    children: [new TextRun({ text: "Деплой, бэкапы, мониторинг, troubleshooting, rollback", italics: true, size: 22, font: FONT, color: "555555" })],
  }),
  new Paragraph({ children: [new PageBreak()] }),

  // 1. Назначение
  H1("1. Назначение документа"),
  P("Документ описывает повседневную эксплуатацию production-инстанса social_inbox. Аудитория — Виктор."),
  P("Установка с нуля — в Setup_Guide.docx. Процедура запуска на реальную аудиторию (canary) — в Go_Live_Checklist.docx + docs/go_live_runbook.md."),

  P("Все команды предполагают:"),
  Bullet([{ text: "Рабочая директория /opt/social_inbox на VPS" }]),
  Bullet([{ text: "Пользователь deploy (член группы docker)" }]),
  Bullet([{ text: ".env и .env.compose заполнены" }]),

  Info([{ text: "Сокращение далее по тексту: ", bold: true }, { text: "DCP = docker compose --env-file .env.compose -f docker-compose.yml -f deploy/docker-compose.prod.yml" }]),

  HR(),

  // 2. Основные команды
  H1("2. Основные команды"),

  H3("2.1. Статус контейнеров"),
  ...Code(`DCP ps
# должно быть 6 контейнеров: api, worker, admin, postgres, redis, traefik
# статус для всех — Up (healthy)`),

  H3("2.2. Логи"),
  ...Code(`DCP logs -f api worker          # хвостить api + worker
DCP logs --tail 200 api          # последние 200 строк api
DCP logs --since 1h worker       # последний час worker
DCP logs -f traefik              # Traefik (для проверки HTTPS / certs)`),

  H3("2.3. Перезапуск"),
  ...Code(`DCP restart                      # рестарт всего стека
DCP restart api                  # рестарт одного сервиса
DCP restart worker               # после редкого зависания polling'а`),

  Warning([
    { text: "Postgres рестартить ", bold: true },
    { text: "только при апгрейде версии или изменении конфигурации. Каждый рестарт — fsync overhead." },
  ]),

  H3("2.4. Полная остановка / запуск"),
  ...Code(`DCP stop          # остановить (volumes сохранены, данные не теряются)
DCP up -d         # запустить обратно`),

  H3("2.5. Shell в контейнер"),
  ...Code(`DCP exec api bash
DCP exec postgres psql -U social_inbox social_inbox
DCP exec redis redis-cli`),

  HR(),

  // 3. Деплой
  H1("3. Деплой новой версии"),
  P("Стандартная процедура — скрипт deploy.sh, который делает env_check + git pull + build + up + smoke check:"),
  ...Code(`cd /opt/social_inbox
./deploy/scripts/deploy.sh`),
  P("Шаги внутри deploy.sh:"),
  Bullet([{ text: "1. env_check.sh — проверка наличия всех ключей в .env и .env.compose" }]),
  Bullet([{ text: "2. git pull --ff-only — забрать новые коммиты из main" }]),
  Bullet([{ text: "3. docker compose build — пересобрать образы" }]),
  Bullet([{ text: "4. docker compose up -d — поднять новые контейнеры" }]),
  Bullet([{ text: "5. sleep 30 — подождать healthchecks" }]),
  Bullet([{ text: "6. smoke_check.sh — проверка /health, /ready, /admin, webhook GET" }]),
  Warning([
    { text: "deploy.sh использует git pull --ff-only ", bold: true },
    { text: "— если на VPS были локальные правки, FF не пройдёт. Это намеренная защита от молчаливого merge-commit'а. Закоммить или отброс правки перед деплоем." },
  ]),

  H3("3.1. Деплой конкретной ветки/тэга"),
  ...Code(`cd /opt/social_inbox
git fetch
git checkout <branch-или-tag>
DCP build
DCP up -d
./deploy/scripts/smoke_check.sh`),

  H3("3.2. Что НЕ обновляется автоматически"),
  Bullet([{ text: "Миграции БД — Alembic запускается при старте api контейнера (см. app/repos/pool.py:run_migrations)" }]),
  Bullet([{ text: ".env / .env.compose — нужно править вручную при изменениях" }]),
  Bullet([{ text: "Traefik static config (deploy/traefik/traefik.yml) — рестартом traefik сервиса" }]),

  HR(),

  // 4. Бэкапы
  H1("4. Бэкапы"),

  H3("4.1. Автоматический ежедневный"),
  P("Crontab запускает /opt/social_inbox/deploy/backup/backup.sh каждый день в 03:00 UTC."),
  P("Что делает скрипт:"),
  Bullet([{ text: "pg_dump социал-инбокса -> /var/backups/social_inbox/daily/social_inbox-YYYY-MM-DD.sql.gz" }]),
  Bullet([{ text: "По воскресеньям копия в /var/backups/social_inbox/weekly/" }]),
  Bullet([{ text: "Retention: 14 дневных + 4 недельных (старые удаляются автоматически)" }]),
  Bullet([{ text: "Если RCLONE_REMOTE задан — rclone copy на offsite" }]),
  Bullet([{ text: "Sanity: если dump < 1KB — удаляет файл и exit 1 (защита от записи битых дампов)" }]),

  H3("4.2. Ручной запуск"),
  ...Code(`cd /opt/social_inbox
./deploy/backup/backup.sh
ls -lh /var/backups/social_inbox/daily/  # последний файл — текущий`),

  H3("4.3. Проверка, что cron работает"),
  ...Code(`crontab -l | grep backup.sh         # строка с расписанием
sudo tail -30 /var/log/social_inbox_backup.log  # последние запуски
ls -lh /var/backups/social_inbox/daily/ | tail -3  # 3 последних файла`),

  H3("4.4. Проверка читаемости dump'а"),
  ...Code(`gunzip -c /var/backups/social_inbox/daily/social_inbox-2026-05-22.sql.gz | head -30
# должны быть -- PostgreSQL database dump ... DROP TABLE IF EXISTS ...`),

  H3("4.5. Offsite (rclone)"),
  ...Code(`echo $RCLONE_REMOTE                          # должно быть задано (например b2:social-inbox-backups)
rclone ls $RCLONE_REMOTE/social_inbox/daily/ | tail -5
# 5 последних копий на удалённом хранилище`),
  Warning([
    { text: "Бэкапы содержат PII. ", bold: true },
    { text: "Минимум — server-side encryption (KMS), лучше — gpg перед заливкой." },
  ]),

  HR(),

  // 5. Restore
  H1("5. Restore из бэкапа"),
  P("Восстановление DESTRUCTIVE — DROP + recreate БД. Делать только вручную, не через cron."),
  ...Code(`cd /opt/social_inbox
ls /var/backups/social_inbox/daily/   # найти нужный файл
./deploy/backup/restore.sh /var/backups/social_inbox/daily/social_inbox-2026-05-21.sql.gz`),
  P("Что делает restore.sh:"),
  Bullet([{ text: "Спрашивает подтверждение (Type 'yes' to confirm)" }]),
  Bullet([{ text: "Останавливает api, worker, admin (postgres + redis остаются)" }]),
  Bullet([{ text: "gunzip + psql из dump'а" }]),
  Bullet([{ text: "Запускает api, worker, admin обратно" }]),
  Bullet([{ text: "Печатает URL для smoke-проверки" }]),
  P("После restore:"),
  ...Code(`curl https://inbox.<домен>/ready
./deploy/scripts/smoke_check.sh
# Проверить админку и логи api на ошибки`),

  HR(),

  // 6. Monitoring
  H1("6. Мониторинг"),

  H3("6.1. Healthcheck endpoints"),
  Bullet([{ text: "/health — процесс жив (всегда 200 если api работает)" }]),
  Bullet([{ text: "/ready/quick — postgres + redis (быстрая, для LB)" }]),
  Bullet([{ text: "/ready — postgres + redis + worker heartbeat (полная, для мониторинга)" }]),

  H3("6.2. Worker heartbeat"),
  P("Worker пишет timestamp в Redis раз в 60 секунд. /ready возвращает 503 если heartbeat старше 180 секунд."),
  ...Code(`DCP exec redis redis-cli GET worker:heartbeat
# ISO datetime; не должен быть старше 1-2 минут`),

  H3("6.3. Sentry (если настроен)"),
  P("Все необработанные exceptions из api и worker автоматически отправляются в Sentry. Зайти в проект на sentry.io и посмотреть recent issues."),
  P("Проверить вручную:"),
  ...Code(`DCP exec api python -c "
import sentry_sdk
sentry_sdk.capture_message('Test from ops runbook', level='info')
"
# В Sentry dashboard должно появиться в течение 2 минут`),

  H3("6.4. Telegram alerts"),
  P("Что приходит Юле в Telegram:"),
  Bullet([{ text: "handover-нотификации (пользователь попросил оператора или AI escalated)" }]),
  Bullet([{ text: "alerts от watchdog (worker умер, очередь растёт, частые parse errors)" }]),
  Bullet([{ text: "daily digest в 09:00–10:00 по местному времени (07:00 UTC)" }]),

  P("Проверить отправку вручную:"),
  ...Code(`DCP exec api python -c "
import asyncio
from app.services.notifications import notify_admin
asyncio.run(notify_admin('Test from ops runbook'))
"
# Юля получает в течение 30 секунд`),

  H3("6.5. Smoke checks"),
  P("Локально (с машины Виктора, не с VPS):"),
  ...Code(`INTERNAL_API_TOKEN=<значение> \\
PROD_BASE_URL=https://inbox.<домен> \\
PROD_ADMIN_URL=https://inbox-admin.<домен> \\
make smoke-prod
# 8 шагов, все должны быть [OK]`),
  P("С VPS:"),
  ...Code(`cd /opt/social_inbox
./deploy/scripts/smoke_check.sh`),

  HR(),

  // 7. Admin dashboard
  H1("7. Admin dashboard"),
  P("Streamlit-приложение на https://inbox-admin.<домен>, Basic Auth по ADMIN_BASIC_AUTH_USER / ADMIN_BASIC_AUTH_PASSWORD."),
  P("Юля использует для:"),
  Bullet([{ text: "Просмотр входящих диалогов, ответы на handover" }]),
  Bullet([{ text: "Управление keywords / сценариями (активация, деактивация)" }]),
  Bullet([{ text: "Per-user переключатель AI режима (выключить для проблемного пользователя)" }]),
  Bullet([{ text: "Статистика по лидам и conversion" }]),

  H3("7.1. Логи админки"),
  ...Code(`DCP logs -f admin`),

  H3("7.2. Сброс пароля"),
  P("Поменять ADMIN_BASIC_AUTH_PASSWORD в .env и рестартнуть admin:"),
  ...Code(`nano /opt/social_inbox/.env
DCP restart admin`),

  HR(),

  // 8. Troubleshooting
  H1("8. Troubleshooting"),

  H3("8.1. Traefik не получает Let's Encrypt сертификат"),
  Bullet([{ text: "DNS A-records указывают на правильный IP VPS (dig +short inbox.<домен>)" }]),
  Bullet([{ text: "Порты 80 и 443 открыты (firewall, cloud security group)" }]),
  Bullet([{ text: "acme.json имеет права 600 (ls -l deploy/traefik/acme.json)" }]),
  Bullet([{ text: "TRAEFIK_ACME_EMAIL в .env.compose заполнен валидным email" }]),
  P("Сброс ACME state (только если сертификаты полностью сломались):"),
  ...Code(`DCP stop traefik
rm deploy/traefik/acme.json
touch deploy/traefik/acme.json
chmod 600 deploy/traefik/acme.json
DCP start traefik
DCP logs -f traefik | grep -i "certificate\\|acme"`),
  Warning([
    { text: "Let's Encrypt rate-limits ", bold: true },
    { text: "— 5 неудачных попыток / час / домен. Не дёргать сертификаты в цикле." },
  ]),

  H3("8.2. bot_purify не может достучаться до social_inbox"),
  P("Проверить сеть:"),
  ...Code(`docker network inspect purify-shared
# В Containers должны быть и social-inbox-api, и bot_purify-bot (или аналог)`),
  P("Из контейнера bot_purify:"),
  ...Code(`docker compose -f /opt/bot_purify/docker-compose.yml exec bot \\
  curl -i -H "X-Internal-Token: $SOCIAL_INBOX_API_TOKEN" \\
  http://social-inbox-api:8000/api/lead/nonexistent
# Ожидаемо: 404 Not Found
# Если connection refused — bot_purify не в сети purify-shared`),
  P("Если не в сети — применить overlay:"),
  ...Code(`cd /opt/bot_purify
docker compose -f docker-compose.yml \\
  -f /opt/social_inbox/deploy/docker-compose.bot-purify.yml up -d`),

  H3("8.3. Worker не обрабатывает события"),
  ...Code(`DCP logs --tail 200 worker
DCP exec redis redis-cli GET worker:heartbeat
DCP exec redis redis-cli LLEN arq:queue
# Если очередь растёт, а в логах тишина — рестартни worker
DCP restart worker`),
  P("Если worker падает на старте — посмотри полный stack trace, чаще всего проблема в .env (опечатка в имени переменной либо неправильный POSTGRES_DSN)."),

  H3("8.4. SendPulse polling: 'comments unavailable free tier'"),
  P("Это нормальное info-сообщение на Free tier — endpoint /instagram/comments недоступен, polling работает только по DM. Не warning."),
  ...Code(`DCP logs worker --tail 100 | grep sendpulse_polling
# sendpulse_polling_start since=...
# sendpulse_comments_unavailable_free_tier
# sendpulse_polling_done events_count=N`),

  H3("8.5. Sentry / Telegram не работают"),
  ...Code(`./deploy/scripts/env_check.sh   # все ли ключи на месте

# Тест отправки в Telegram
DCP exec api python -c "
import asyncio
from app.services.notifications import notify_admin
asyncio.run(notify_admin('Test'))
"

# Тест Sentry
DCP exec api python -c "
import sentry_sdk
sentry_sdk.capture_message('Test', level='info')
"`),

  H3("8.6. Диск переполнен"),
  ...Code(`df -h
du -sh /var/lib/docker /var/backups/social_inbox /var/log

# Если docker volumes пухнут — Postgres логи
# Возможные решения:
#  1. DCP exec postgres psql -U social_inbox -c "VACUUM FULL;"
#  2. Включить log rotation в postgresql.conf
#  3. Удалить старые offsite-копии локально (rclone уже хранит)`),

  H3("8.7. Claude отвечает странно или нарушает safety"),
  ...Code(`DCP exec postgres psql -U social_inbox social_inbox -c "
SELECT text, safety_blocked, safety_reason
FROM messages
WHERE direction='out'
ORDER BY created_at DESC LIMIT 20;
"
# Если много safety_blocked=TRUE — улучшить system prompt
# app/prompts/system_smart.md, рестартнуть worker`),

  HR(),

  // 9. Rollback
  H1("9. Rollback"),

  H3("9.1. Откат кода"),
  ...Code(`cd /opt/social_inbox
git log --oneline -10                # найти предыдущий рабочий коммит
git reset --hard <sha-предыдущего>
./deploy/scripts/deploy.sh`),
  Warning([
    { text: "git reset --hard ", bold: true },
    { text: "удаляет всё что после <sha>. Делать только при полной уверенности или при отсутствии локальных изменений." },
  ]),

  H3("9.2. Откат данных"),
  P("Восстановить БД из последнего хорошего бэкапа (см. раздел 5)."),
  Warning([
    { text: "Restore теряет ВСЕ изменения ", bold: true },
    { text: "сделанные после момента создания бэкапа. Использовать только в случае серьёзной порчи данных." },
  ]),

  H3("9.3. Частичное отключение функциональности (без полного rollback)"),
  P("Через админку:"),
  Bullet([{ text: "Отключить отдельный keyword: Ключевые слова -> снять галочку Активен" }]),
  Bullet([{ text: "Отключить smart-replies: Сценарии -> default_smart -> снять Активен (engine падает в echo fallback)" }]),
  Bullet([{ text: "Отключить AI для конкретного абьюзивного юзера: открыть диалог -> переключатель AI в выключенный" }]),

  P("Через SendPulse:"),
  Bullet([{ text: "Отключить webhook одной кнопкой в SendPulse UI (если используется paid tier с webhooks)" }]),
  Bullet([{ text: "На Free tier (polling): SENDPULSE_POLLING_ENABLED=false в .env + рестарт worker" }]),

  P("Полная остановка (emergency):"),
  ...Code(`DCP stop
# Подписчики получат connection refused на webhook'и SendPulse — они ретрайнут пару раз и перестанут
# Данные в БД сохранены, запустить обратно: DCP up -d`),

  HR(),

  // 10. Security checklist
  H1("10. Security checklist"),
  P("Проверяй периодически (раз в месяц):"),
  Bullet([{ text: "[ ] .env и .env.compose имеют chmod 600" }]),
  Bullet([{ text: "[ ] acme.json имеет chmod 600" }]),
  Bullet([{ text: "[ ] Postgres-порт (5432) НЕ exposed публично (только через docker network)" }]),
  Bullet([{ text: "[ ] Redis-порт (6379) НЕ exposed публично" }]),
  Bullet([{ text: "[ ] Traefik dashboard выключен (api.dashboard: false в traefik.yml)" }]),
  Bullet([{ text: "[ ] Админка за Basic Auth (curl -I https://inbox-admin.<домен> -> 401 без credentials)" }]),
  Bullet([{ text: "[ ] HTTPS на обоих доменах, HTTP редиректит на HTTPS" }]),
  Bullet([{ text: "[ ] SSH key authentication only (password login отключён)" }]),
  Bullet([{ text: "[ ] unattended-upgrades включён (security patches)" }]),
  Bullet([{ text: "[ ] Бэкапы имеют offsite копию (rclone copy успешно проходит)" }]),
  Bullet([{ text: "[ ] INTERNAL_API_TOKEN ни в логах, ни в process list (только в .env)" }]),

  HR(),

  // 11. Common issues first week
  H1("11. Типичные проблемы первой недели"),

  H3("11.1. Юля жалуется на спам уведомлений в Telegram"),
  P("Возможные причины:"),
  Bullet([{ text: "Слишком короткий dedup TTL для алертов — см. app/observability/alerts.py" }]),
  Bullet([{ text: "Дублирующиеся handover-триггеры — проверить keywords с типом handover" }]),
  Bullet([{ text: "Watchdog шлёт алерты при коротких сбоях — увеличить threshold" }]),

  H3("11.2. Conversion (Instagram -> Telegram) низкий"),
  ...Code(`DCP exec postgres psql -U social_inbox social_inbox -c "
SELECT text FROM messages
WHERE direction='out' AND created_at > NOW() - INTERVAL '1 day';
"
# Проверить deep-link в исходящих
# https://t.me/yuliya_purify_bot?start=ig_<short_id>_<scenario_slug>`),
  P("Если ссылка кривая — проверить TELEGRAM_BOT_USERNAME в .env."),
  P("Если ссылка валидная, но люди не нажимают — улучшать welcome-сообщение в админке (Сценарии -> default_welcome)."),

  H3("11.3. SendPulse rate limits (429)"),
  P("Free tier: 5 req/sec. При большой нагрузке worker может ловить 429. Решения:"),
  Bullet([{ text: "Увеличить SENDPULSE_POLLING_INTERVAL_SECONDS с 30 до 60 (но обновить cron в arq_settings.py — second={0,30} -> second={0})" }]),
  Bullet([{ text: "Апгрейд тарифа SendPulse" }]),

  H3("11.4. Anthropic rate limit / token usage"),
  ...Code(`DCP exec postgres psql -U social_inbox social_inbox -c "
SELECT SUM(claude_tokens_in + claude_tokens_out) AS total_tokens
FROM messages
WHERE created_at > NOW() - INTERVAL '24 hours';
"
# Если тратим больше ожидаемого:
# - Уменьшить max_tokens в app/services/claude_responder.py
# - Переключить дефолтную модель на cheaper (sonnet -> haiku)`),

  HR(),

  // 12. Контакты
  H1("12. Контакты для emergency"),
  P([{ text: "Юля: ", bold: true }, { text: "Telegram (см. NOTIFICATION_ADMIN_CHAT_ID)" }]),
  P([{ text: "SendPulse поддержка: ", bold: true }, { text: "support@sendpulse.com" }]),
  P([{ text: "Anthropic поддержка: ", bold: true }, { text: "support@anthropic.com" }]),
  P([{ text: "Hosting VPS: ", bold: true }, { text: "<вписать провайдера и контакт>" }]),
  P([{ text: "DNS-провайдер: ", bold: true }, { text: "<вписать>" }]),

  HR(),

  // 13. Дальше
  H1("13. Дальнейшее развитие"),
  P("Backlog возможных улучшений (после стабилизации первого месяца):"),
  Bullet([{ text: "A/B testing разных welcome-сообщений" }]),
  Bullet([{ text: "Vision-mode Claude (когда SendPulse начнёт присылать изображения)" }]),
  Bullet([{ text: "TikTok DM (когда они откроют API)" }]),
  Bullet([{ text: "Многоязычность (Polish, Lithuanian) когда расширится аудитория" }]),
  Bullet([{ text: "Миграция с SendPulse на собственное Meta App (требует юрлица)" }]),
  Bullet([{ text: "Apgreyd SendPulse на платный тариф — переключение polling -> webhook через одну переменную" }]),

  Success([
    { text: "Финальная заметка: ", bold: true },
    { text: "большинство инцидентов решается одним из трёх — restart worker, deactivate keyword в админке, restore из бэкапа. Делай эти три действия уверенно, остальное — отдельные расследования." },
  ]),
];

const doc = new Document({
  creator: "Claude",
  title: "social_inbox — Operations Guide",
  styles: {
    default: { document: { run: { font: FONT, size: 22 } } },
  },
  numbering: {
    config: [
      { reference: "bullets",
        levels: [
          { level: 0, format: LevelFormat.BULLET, text: "*", alignment: AlignmentType.LEFT,
            style: { paragraph: { indent: { left: 720, hanging: 360 } } } },
        ] },
    ],
  },
  sections: [{
    properties: {
      page: {
        size: { width: 11906, height: 16838 },
        margin: { top: 1440, right: 1440, bottom: 1440, left: 1440 },
      },
    },
    headers: {
      default: new Header({
        children: [new Paragraph({
          alignment: AlignmentType.RIGHT,
          children: [new TextRun({ text: "social_inbox · Operations Guide", italics: true, color: GRAY, size: 18, font: FONT })],
        })],
      }),
    },
    footers: {
      default: new Footer({
        children: [new Paragraph({
          alignment: AlignmentType.CENTER,
          children: [new TextRun({ children: ["Стр. ", PageNumber.CURRENT, " из ", PageNumber.TOTAL_PAGES], size: 18, font: FONT, color: GRAY })],
        })],
      }),
    },
    children,
  }],
});

Packer.toBuffer(doc).then(buf => {
  const out = process.argv[2] || "../Doc/Operations_Guide.docx";
  fs.writeFileSync(out, buf);
  console.log("OK:", out, "size:", buf.length);
});
