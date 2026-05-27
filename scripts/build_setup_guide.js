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

const Step = (n, text) => new Paragraph({
  spacing: { before: 200, after: 80 },
  children: [
    new TextRun({ text: `Шаг ${n}. `, bold: true, size: 24, font: FONT, color: ORANGE }),
    new TextRun({ text, bold: true, size: 24, font: FONT }),
  ],
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
    children: [new TextRun({ text: "Инструкция по настройке и запуску", size: 32, font: FONT })],
  }),
  new Paragraph({
    alignment: AlignmentType.CENTER,
    spacing: { after: 200 },
    children: [new TextRun({ text: "Разворачивание production-стека с нуля на VPS", italics: true, size: 22, font: FONT, color: "555555" })],
  }),
  new Paragraph({ children: [new PageBreak()] }),

  // 1. Назначение
  H1("1. Назначение документа"),
  P("Документ описывает первичное разворачивание social_inbox в production. Аудитория — Виктор (DevOps/инженер)."),
  P("Эксплуатация (повседневный деплой, бэкапы, мониторинг, troubleshooting) описана в отдельном документе: Operations_Guide.docx."),
  P("Запуск на реальную аудиторию (canary rollout) — в Go_Live_Checklist.docx (для Юли) и docs/go_live_runbook.md (для Виктора)."),

  HR(),

  // 2. Архитектура
  H1("2. Архитектура production"),
  P("На VPS поднимаются 6 контейнеров через docker compose + production overlay:"),
  Bullet([{ text: "traefik (80/443) — HTTPS-роутер с автоматическим Let's Encrypt" }]),
  Bullet([{ text: "api — FastAPI, принимает webhooks от SendPulse, отдаёт /api/lead для bot_purify" }]),
  Bullet([{ text: "worker — arq, обрабатывает события, polling SendPulse, отправка ответов" }]),
  Bullet([{ text: "admin — Streamlit dashboard для Юли" }]),
  Bullet([{ text: "postgres 16 — основная БД (named volume postgres_data, переживает рестарты)" }]),
  Bullet([{ text: "redis 7 — arq queue + token cache + cursor polling" }]),
  P([{ text: "Публичные URL:" }]),
  Bullet([{ text: "https://inbox.<domain> — webhook + /api/lead (через Traefik)" }]),
  Bullet([{ text: "https://inbox-admin.<domain> — админка (Basic Auth, через Traefik)" }]),
  P("bot_purify в отдельном compose подключается к этому стеку через external docker network 'purify-shared' и ходит на http://social-inbox-api:8000."),

  HR(),

  // 3. Предусловия
  H1("3. Предусловия"),

  H3("3.1. Внешние сервисы и доступы"),
  Bullet([{ text: "VPS: Ubuntu 22.04+ или Debian 12+, минимум 2 GB RAM, 20 GB диск, статический IP, SSH-доступ" }]),
  Bullet([{ text: "Домен: должен позволять настраивать A-records (inbox, inbox-admin)" }]),
  Bullet([{ text: "SendPulse аккаунт: Free tier, бот для Instagram подключён к аккаунту Юли (Task 02)" }]),
  Bullet([{ text: "Anthropic API key: console.anthropic.com -> Settings -> API Keys" }]),
  Bullet([{ text: "Telegram bot для уведомлений: создан через @BotFather (NOTIFICATION_BOT_TOKEN)" }]),
  Bullet([{ text: "Telegram chat_id админа: numeric ID Юли (NOTIFICATION_ADMIN_CHAT_ID)" }]),
  Bullet([{ text: "Sentry проект (опционально, но рекомендуется): sentry.io -> создать проект 'social_inbox' -> DSN" }]),
  Bullet([{ text: "(Опционально) Backblaze B2 / S3 / Google Drive аккаунт для offsite-бэкапов через rclone" }]),

  H3("3.2. Локально (у Виктора)"),
  Bullet([{ text: "Git, SSH-ключ, доступ к репо github.com/5c00p/social_inbox" }]),
  Bullet([{ text: "(Опционально) Node.js 18+ если планируешь пересобирать Go_Live_Checklist.docx" }]),

  HR(),

  // 4. Шаги
  H1("4. Пошаговая настройка"),

  Step(1, "Подготовка VPS"),
  P("Установить Docker Engine + docker compose plugin:"),
  ...Code(`# Ubuntu / Debian
curl -fsSL https://get.docker.com | sudo bash
sudo usermod -aG docker $USER
newgrp docker
docker compose version  # должно быть v2.20+`),

  P("Создать deploy-пользователя (НЕ работать под root):"),
  ...Code(`sudo adduser deploy
sudo usermod -aG docker deploy
sudo -iu deploy
# дальше всё под deploy@vps`),

  Warning([
    { text: "Требуется docker compose v2.20+ ", bold: true },
    { text: "— production overlay использует !override теги, которые в более старых версиях не работают." },
  ]),

  Step(2, "DNS records"),
  P("В DNS-провайдере создать два A-record'а, указывающих на IP VPS:"),
  ...Code(`inbox.<твой-домен>          A    <IP-VPS>
inbox-admin.<твой-домен>    A    <IP-VPS>`),
  P("Проверить распространение (может занять до 30 минут):"),
  ...Code(`dig +short inbox.<домен>
dig +short inbox-admin.<домен>
# должны вернуть <IP-VPS>`),

  Step(3, "Клонирование репозитория"),
  ...Code(`sudo mkdir -p /opt/social_inbox
sudo chown deploy:deploy /opt/social_inbox
cd /opt/social_inbox
git clone https://github.com/5c00p/social_inbox.git .
git checkout main`),

  Step(4, "Заполнение env-файлов"),
  P("Прод использует ДВА env-файла, чтобы не конфликтовать с Settings(extra='forbid'):"),
  Bullet([{ text: ".env — переменные приложения, загружаются в api/admin/worker через env_file" }]),
  Bullet([{ text: ".env.compose — переменные для docker compose substitution: POSTGRES_USER/PASSWORD/DB, PUBLIC_HOST_*, TRAEFIK_ACME_EMAIL" }]),

  ...Code(`cp deploy/.env.prod.example .env
cp deploy/.env.compose.example .env.compose
nano .env          # заполнить app-переменные
nano .env.compose  # заполнить compose-переменные
chmod 600 .env .env.compose`),

  P("Ключи в .env, которые ОБЯЗАТЕЛЬНО заполнить:"),
  Bullet([{ text: "POSTGRES_DSN — должен содержать тот же пароль, что POSTGRES_PASSWORD в .env.compose" }]),
  Bullet([{ text: "SENDPULSE_CLIENT_ID / SENDPULSE_CLIENT_SECRET — из SendPulse -> Settings -> API" }]),
  Bullet([{ text: "SENDPULSE_BOT_ID — из URL бота в SendPulse (или поле Bot ID в настройках)" }]),
  Bullet([{ text: "ANTHROPIC_API_KEY — из console.anthropic.com" }]),
  Bullet([{ text: "INTERNAL_API_TOKEN — длинная случайная строка (та же должна быть в bot_purify .env)" }]),
  Bullet([{ text: "ADMIN_BASIC_AUTH_USER / ADMIN_BASIC_AUTH_PASSWORD — Юлин логин/пароль в админку" }]),
  Bullet([{ text: "NOTIFICATION_BOT_TOKEN — токен бота от @BotFather для алертов" }]),
  Bullet([{ text: "NOTIFICATION_ADMIN_CHAT_ID — numeric chat_id Юли в Telegram" }]),
  Bullet([{ text: "PUBLIC_BASE_URL — https://inbox.<домен>" }]),
  Bullet([{ text: "SENTRY_DSN — из Sentry-проекта (если используется)" }]),

  P("Ключи в .env.compose:"),
  Bullet([{ text: "POSTGRES_USER / POSTGRES_PASSWORD / POSTGRES_DB" }]),
  Bullet([{ text: "PUBLIC_HOST_INBOX — inbox.<домен> (без https://)" }]),
  Bullet([{ text: "PUBLIC_HOST_ADMIN — inbox-admin.<домен>" }]),
  Bullet([{ text: "TRAEFIK_ACME_EMAIL — email для Let's Encrypt уведомлений" }]),

  P("Проверить, что все ключи на месте:"),
  ...Code(`./deploy/scripts/env_check.sh
# OK: .env + .env.compose have all required keys`),

  Warning([
    { text: "POSTGRES_PASSWORD в .env.compose и пароль в POSTGRES_DSN в .env ", bold: true },
    { text: "должны совпадать байт-в-байт, иначе api/worker не смогут подключиться к Postgres." },
  ]),

  Step(5, "Создание external docker network"),
  P("Сеть 'purify-shared' разделяется с bot_purify, чтобы они могли видеть друг друга:"),
  ...Code(`docker network create purify-shared`),
  Info([{ text: "Если bot_purify уже развёрнут с собственной сетью, её нужно либо пересоздать с именем 'purify-shared', либо подключить bot_purify к 'purify-shared' как external (см. deploy/docker-compose.bot-purify.yml)." }]),

  Step(6, "ACME storage для Traefik"),
  P("Файл для хранения Let's Encrypt сертификатов:"),
  ...Code(`touch deploy/traefik/acme.json
chmod 600 deploy/traefik/acme.json`),
  Warning([
    { text: "chmod 600 ОБЯЗАТЕЛЕН ", bold: true },
    { text: "— Traefik откажется использовать acme.json с правами 644/755 и сертификаты не получатся." },
  ]),

  Step(7, "Первый запуск"),
  ...Code(`docker compose --env-file .env.compose \\
  -f docker-compose.yml -f deploy/docker-compose.prod.yml up -d`),

  P("Подождать 2–3 минуты и посмотреть логи:"),
  ...Code(`docker compose --env-file .env.compose \\
  -f docker-compose.yml -f deploy/docker-compose.prod.yml logs -f traefik api`),

  P("В логах Traefik искать строку 'obtained certificate' для обоих доменов. Когда сертификаты получены — проверить:"),
  ...Code(`curl https://inbox.<домен>/health
# {"status":"ok"}

curl https://inbox.<домен>/ready
# {"status":"ready","postgres":"up","redis":"up","worker":{"status":"up",...}}`),

  Info([
    { text: "Если /ready возвращает worker:down ", bold: true },
    { text: "— подождать 90 секунд (worker пишет heartbeat раз в минуту, freshness threshold 180с)." },
  ]),

  Step(8, "Smoke-проверки"),
  P("С локальной машины (НЕ с VPS) — пройдут через CDN/DNS:"),
  ...Code(`INTERNAL_API_TOKEN=<значение-из-.env-на-VPS> \\
PROD_BASE_URL=https://inbox.<домен> \\
PROD_ADMIN_URL=https://inbox-admin.<домен> \\
make smoke-prod`),
  P("Должны быть [OK] на всех восьми шагах (health, ready/quick, ready/full, webhook-verify, webhook-post, lead-api-auth, lead-api-404, https-redirect)."),

  Step(9, "SendPulse polling (включён по умолчанию)"),
  P("На Free tier у SendPulse webhook'и недоступны, поэтому работаем через polling. В .env должно быть:"),
  ...Code(`SENDPULSE_POLLING_ENABLED=true
SENDPULSE_POLLING_INTERVAL_SECONDS=30
SENDPULSE_BOT_ID=<id-из-SendPulse>`),
  P("Worker дёргает /instagram/messages + /instagram/comments раз в 30 секунд. В логах должно появляться:"),
  ...Code(`docker compose logs worker --tail 100 | grep sendpulse_polling
# sendpulse_polling_start since=...
# sendpulse_polling_done events_count=0 new_cursor=...`),
  Info([
    { text: "При апгрейде SendPulse до платного тарифа: ", bold: true },
    { text: "выставить SENDPULSE_POLLING_ENABLED=false в .env, в SendPulse UI настроить webhook URL https://inbox.<домен>/webhooks/sendpulse, рестартнуть worker. Polling выключится, webhook'и сами начнут работать." },
  ]),

  Step(10, "Подключение bot_purify"),
  P("В /opt/bot_purify/.env прописать:"),
  ...Code(`SOCIAL_INBOX_API_URL=http://social-inbox-api:8000
SOCIAL_INBOX_API_TOKEN=<значение INTERNAL_API_TOKEN из social_inbox/.env>`),

  P("Применить overlay, чтобы bot_purify зашёл в сеть purify-shared:"),
  ...Code(`cd /opt/bot_purify
docker compose -f docker-compose.yml \\
  -f /opt/social_inbox/deploy/docker-compose.bot-purify.yml up -d`),

  P("Проверить связь из контейнера bot_purify:"),
  ...Code(`docker compose -f /opt/bot_purify/docker-compose.yml exec bot \\
  curl -i -H "X-Internal-Token: $SOCIAL_INBOX_API_TOKEN" \\
  http://social-inbox-api:8000/api/lead/nonexistent
# Ожидаемо: HTTP 404 (не connection refused)`),

  Step(11, "Cron для ежедневных бэкапов"),
  ...Code(`sudo mkdir -p /var/backups/social_inbox /var/log
sudo touch /var/log/social_inbox_backup.log
sudo chown deploy /var/log/social_inbox_backup.log /var/backups/social_inbox

crontab -e
# Вставить строки из deploy/backup/crontab.example:
# 0 3 * * * /opt/social_inbox/deploy/backup/backup.sh >> /var/log/social_inbox_backup.log 2>&1`),
  P("Запустить бэкап вручную для проверки:"),
  ...Code(`./deploy/backup/backup.sh
ls -lh /var/backups/social_inbox/daily/  # появится файл social_inbox-YYYY-MM-DD.sql.gz`),

  Step(12, "(Опционально) Offsite backup через rclone"),
  ...Code(`# Установить rclone, настроить remote (b2/s3/gdrive)
rclone config

# Прописать переменную (или в bashrc для глобального доступа)
echo 'export RCLONE_REMOTE="b2:social-inbox-backups"' >> ~/.bashrc
source ~/.bashrc

# backup.sh подхватит RCLONE_REMOTE и автоматически синкнет на offsite`),
  Warning([
    { text: "Бэкапы содержат PII ", bold: true },
    { text: "(имена, тексты сообщений). Минимум — server-side encryption с KMS, лучше — клиентское шифрование gpg перед загрузкой." },
  ]),

  HR(),

  // 5. Финал
  H1("5. Что должно работать после Шага 12"),
  Success([
    { text: "Production развёрнут полностью. ", bold: true },
    { text: "На этом этапе сервис принимает webhooks (или работает polling-ом), отдаёт /api/lead в bot_purify, бэкапится ежедневно, ACME продлевает сертификаты автоматически." },
  ]),

  Bullet([{ text: "[OK] HTTPS на inbox.<домен> и inbox-admin.<домен>, HTTP редиректит на HTTPS" }]),
  Bullet([{ text: "[OK] Юля заходит в админку через inbox-admin.<домен> с Basic Auth" }]),
  Bullet([{ text: "[OK] bot_purify обращается к /api/lead/{short_id} по внутренней docker-сети" }]),
  Bullet([{ text: "[OK] Postgres бэкапится ежедневно в 03:00 UTC, retention 14 daily + 4 weekly" }]),
  Bullet([{ text: "[OK] Docker logging rotation: max 10MB на файл, 3-5 файлов на контейнер" }]),
  Bullet([{ text: "[OK] Sentry собирает ошибки, daily digest и handover-алерты приходят Юле в Telegram" }]),
  Bullet([{ text: "[OK] Перезапуск compose не теряет данных (postgres_data volume persistent)" }]),

  P("Запуск на реальную аудиторию — отдельная процедура (canary rollout), см. Go_Live_Checklist.docx + docs/go_live_runbook.md."),
  P("Повседневная эксплуатация — Operations_Guide.docx."),
];

const doc = new Document({
  creator: "Claude",
  title: "social_inbox — Setup Guide",
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
          children: [new TextRun({ text: "social_inbox · Setup Guide", italics: true, color: GRAY, size: 18, font: FONT })],
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
  const out = process.argv[2] || "../Doc/Setup_Guide.docx";
  fs.writeFileSync(out, buf);
  console.log("OK:", out, "size:", buf.length);
});
