# CLAUDE.md — social_inbox

> Единый источник правды для проекта. Этот файл Claude Code читает в начале каждой сессии.
> Если что-то здесь противоречит инструкциям пользователя — приоритет у пользователя, но обязательно укажи на расхождение и предложи обновить CLAUDE.md.
>
> **Версия 2** (после анализа bot_purify и выбора SendPulse Free + n8n).

---

## 1. Контекст и цель проекта

**social_inbox** — централизованный сервис для автоматизированного общения с подписчиками Юлии (`@yulia_purify`, doTERRA-консультант) в Instagram Direct и Facebook Messenger.

**Бизнес-цель:** превратить входящий трафик из соц-сетей в лидов, дошедших до Telegram-бота `@yuliya_purify_bot`, где идёт прогрев и продажа программы «Очищение».

**Что делает сервис:**

1. Принимает webhook-события через **SendPulse** (платформу-прокладку, у которой уже есть одобренное Meta App)
2. Welcome-сообщение при первом DM от пользователя (с deep-link в Telegram)
3. Comment-to-DM: автоматический DM пользователю, написавшему ключевое слово в комментариях под Reels
4. Smart replies через Claude API на типовые вопросы (с эскалацией к человеку)
5. Передача лидов в `@yuliya_purify_bot` через deep link с прокидыванием контекста через REST API

**Что сервис НЕ делает (важно):**

- Не отправляет холодные DM подписчикам — это нарушение Meta Platform Policy
- Не продаёт продукты doTERRA напрямую — только лид-магниты + переход в Telegram
- Не даёт медицинских советов и не делает заявления о лечении — юридический риск для doTERRA
- Не работает с TikTok DM (нет API) и YouTube DM (нет такой функции в YT) — вне scope MVP

---

## 2. Стратегия provider-абстракции

**Ключевое архитектурное решение:** все взаимодействия с messaging-платформами (получение событий, отправка сообщений) идут через интерфейс `MessagingProvider`. Текущая реализация — `SendPulseProvider`, но архитектура предусматривает будущую миграцию.

```
Этап 1 (сейчас): SendPulseProvider — бесплатный план
                 (500 подписчиков, 10k сообщений/мес)
       ↓
Этап 2 (1k+ подписчиков): SendPulseProvider — платный (~$10–15/мес)
                          ИЛИ переключение на ManychatProvider ($15/мес)
       ↓
Этап 3 (когда появится юрлицо): MetaProvider — собственное Meta App,
                                прямая интеграция с Graph API,
                                без посредников
```

**Что меняется при переключении:** один файл провайдера + конфиг. Логика сценариев, БД, Claude integration, Telegram handover, safety-фильтры — всё переиспользуется.

**Что значит «Provider»:**
- Принимает входящие события платформы (через webhook от SendPulse)
- Нормализует их в `IncomingEvent` — общую модель для всей системы
- Принимает запрос на отправку (`OutgoingMessage`) от ScenarioEngine и шлёт в платформу

---

## 3. Связанные системы

| Система | Где код | Связь | Статус |
|---------|---------|-------|--------|
| `@yuliya_purify_bot` | `D:\Work\bot_purify` (aiogram 3.27, Postgres, Redis, Google Sheets mirror) | Принимает лидов через deep-link `?start=ig_<short_id>_<scenario>`. При `/start` дёргает `GET /api/lead/{short_id}` за контекстом. Реализовано в Task `TASK_social_inbox_integration.md`. | После применения task |
| `purify-marathon` (landing) | Vite + React + Tailwind, Netlify | Источник лид-магнитов. social_inbox шлёт ссылки на конкретные секции лендинга. | Существует |
| SendPulse | Внешний SaaS, free tier | Подключён к Instagram + Facebook Юлии через OAuth. Шлёт нам webhooks при входящих сообщениях/комментариях. Принимает наши API-запросы для отправки ответов. | Регистрация — Task 02 |
| n8n self-hosted | В нашем docker-compose | Опционально используется для маркетинговой аналитики и интеграций (Google Sheets, Notion). НЕ для основной логики — основная логика в FastAPI. | Опционально, после MVP |

**Архитектурный пересмотр vs CLAUDE.md v1:** изначально планировалось делать всю логику в n8n, но при детальном проектировании стало ясно, что safety-фильтры doTERRA, типобезопасные модели и интеграция с bot_purify проще писать на Python. n8n остаётся в стеке как опциональный инструмент для marketing-flows, не как основной runtime.

---

## 4. Архитектура

### High-level диаграмма

```
┌──────────────────┐
│ Instagram + FB   │
│  Юлии            │
└────────┬─────────┘
         │
         │ (через OAuth-разрешения,
         │  одобренное Meta App SendPulse)
         ▼
┌──────────────────────────────────────────────────────────┐
│  SendPulse Free                                           │
│  - принимает входящие DM/comments                         │
│  - имеет 25 переменных, 10 триггеров, 10k msg/мес        │
│  - НЕ хранит бизнес-логику (только webhook → нам)        │
└────────┬─────────────────────────────────────────────────┘
         │ webhook POST /webhooks/sendpulse
         ▼
┌──────────────────────────────────────────────────────────┐
│  social_inbox API (FastAPI)                               │
│  ┌────────────────────────────────────────────────┐      │
│  │ MessagingProvider interface                     │      │
│  │   ↓                                             │      │
│  │ SendPulseProvider                               │      │
│  │   - parse incoming event                        │      │
│  │   - normalize → IncomingEvent                   │      │
│  └────────────────────────────────────────────────┘      │
│         │                                                 │
│         ▼ enqueue                                         │
└─────────┼─────────────────────────────────────────────────┘
          │
          ▼
┌──────────────────────────────────────────────────────────┐
│  Redis (queue + cache + rate-limit)                       │
└──────────────────────────────────────────────────────────┘
          │
          ▼
┌──────────────────────────────────────────────────────────┐
│  arq Worker                                               │
│  ┌────────────────────────────────────────────────┐      │
│  │ ScenarioEngine                                  │      │
│  │   1. lookup user / create if first contact      │      │
│  │   2. match keywords → trigger scenario          │      │
│  │   3. or → ClaudeResponder for smart reply       │      │
│  │   4. apply safety filters                       │      │
│  │   5. enqueue OutgoingMessage                    │      │
│  └────────────────────────────────────────────────┘      │
│  ┌────────────────────────────────────────────────┐      │
│  │ MessagingProvider.send(OutgoingMessage)         │      │
│  └────────────────────────────────────────────────┘      │
└──────┬─────────────────┬─────────────────┬───────────────┘
       │                 │                 │
       ▼                 ▼                 ▼
┌───────────────┐ ┌──────────────┐ ┌─────────────────┐
│  Postgres     │ │  Claude API  │ │  SendPulse API  │
│  (local)      │ │              │ │  (для отправки) │
└───────────────┘ └──────────────┘ └─────────────────┘
       │
       │
       ▼
┌──────────────────────────────────────────────────────────┐
│  GET /api/lead/{short_id}                                 │
│  (для bot_purify — отдаёт контекст лида)                  │
└────────┬─────────────────────────────────────────────────┘
         │
         │ HTTP (внутри docker network)
         ▼
┌──────────────────────────────────────────────────────────┐
│  bot_purify (отдельный compose-сервис)                    │
│  - получает /start ig_<short_id>_<scenario>               │
│  - запрашивает контекст из social_inbox                   │
│  - продолжает свой quiz/warmup/order flow                 │
└──────────────────────────────────────────────────────────┘
```

### Ключевые принципы

1. **Webhook → 200 OK за <1 сек.** Реальная обработка — в воркере. SendPulse не делает агрессивных retry, но принцип сохраняем.
2. **Один сервис, один бэкенд, один рантайм.** FastAPI + arq в одном Python-проекте. Не разнесено на микросервисы — проект слишком мал.
3. **Provider-абстракция строго соблюдается.** Никаких прямых вызовов SendPulse SDK в ScenarioEngine. Только через интерфейс.
4. **Idempotency на ID событий SendPulse.** Чтобы дублирующиеся webhooks не приводили к дублирующимся ответам.

---

## 5. Технологический стек

**Зафиксировано — НЕ менять без явного запроса Виктора:**

- **Python 3.12** (как в bot_purify, для совместимости стека)
- **uv** для управления зависимостями
- **FastAPI** + **uvicorn** (для прода — gunicorn с uvicorn workers)
- **httpx** для всех HTTP-вызовов (SendPulse API, Claude API, bot_purify проверки)
- **Pydantic v2** для всех моделей
- **asyncpg** напрямую (как в bot_purify — без SQLAlchemy ORM)
- **Postgres 16** — отдельный контейнер в docker-compose рядом с bot_purify Postgres (разные БД, разные пароли)
- **Alembic** — миграции
- **Redis 7** — очередь arq + кэш + rate limiting (отдельный инстанс от Redis bot_purify)
- **arq** — фоновые задачи
- **anthropic** Python SDK — Claude API
  - `claude-sonnet-4-6` для FAQ-режима (по умолчанию)
  - `claude-opus-4-7` опционально для сложных диалогов (флаг в БД)
- **structlog** — JSON-логи в проде, человекочитаемые в dev
- **sentry-sdk** — мониторинг ошибок
- **Docker Compose** — деплой (одним compose с bot_purify или отдельным — см. § 14)
- **ruff** + **mypy** — линт и типы
- **pytest** + **pytest-asyncio** — тесты
- **nanoid** — генерация short_id (8 символов, URL-safe алфавит без подчёркивания)

**Запрещено без обсуждения:**

- SQLAlchemy ORM (используем чистый SQL через asyncpg, как в bot_purify)
- Django, Flask
- Celery, RQ
- requests (sync) — только httpx async
- Pydantic v1
- SendPulse SDK напрямую вне `SendPulseProvider`

---

## 6. Структура проекта

```
D:\Work\social_inbox\
├── app/
│   ├── __init__.py
│   ├── main.py                   # FastAPI app entry
│   ├── config.py                 # pydantic-settings, читает .env
│   ├── deps.py                   # FastAPI dependencies
│   │
│   ├── api/
│   │   ├── __init__.py
│   │   ├── webhooks.py           # /webhooks/sendpulse (и заглушки для будущих провайдеров)
│   │   ├── lead.py               # /api/lead/{short_id} — для bot_purify
│   │   ├── admin.py              # /api/admin/* — для админки
│   │   ├── health.py             # /health, /ready
│   │   └── data_deletion.py      # /data-deletion (требование Meta, на будущее)
│   │
│   ├── providers/                # ← MessagingProvider абстракция
│   │   ├── __init__.py
│   │   ├── base.py               # MessagingProvider ABC + IncomingEvent + OutgoingMessage
│   │   ├── sendpulse.py          # SendPulseProvider — текущая реализация
│   │   ├── manychat.py           # ManychatProvider — заглушка для будущего
│   │   └── meta.py               # MetaProvider — заглушка для будущего (когда юрлицо)
│   │
│   ├── models/
│   │   ├── __init__.py
│   │   ├── events.py             # Pydantic-модели IncomingEvent, OutgoingMessage
│   │   ├── db.py                 # Pydantic-модели строк БД (для type hints)
│   │   └── enums.py              # Platform, Direction, ScenarioType, ConversationStatus
│   │
│   ├── services/
│   │   ├── __init__.py
│   │   ├── claude_responder.py   # Вызов Claude API + tool use
│   │   ├── keyword_matcher.py    # Матчинг ключевых слов
│   │   ├── scenario_engine.py    # Главная логика: routing rules vs Claude
│   │   ├── lead_tracker.py       # Генерация short_id, deep links, передача в Telegram
│   │   ├── safety.py             # Safety-фильтры (doTERRA medical claims)
│   │   └── rate_limiter.py       # Защита от спама + лимиты SendPulse API
│   │
│   ├── workers/
│   │   ├── __init__.py
│   │   ├── arq_settings.py       # WorkerSettings для arq
│   │   ├── tasks_messages.py     # process_incoming_message
│   │   └── tasks_comments.py     # process_incoming_comment
│   │
│   ├── prompts/
│   │   ├── system_faq.md         # System prompt для FAQ-режима Claude
│   │   ├── system_smart.md       # System prompt для умных ответов
│   │   └── banned_patterns.py    # Список запрещённых медицинских формулировок
│   │
│   ├── repos/                    # Database access layer (raw SQL, как в bot_purify)
│   │   ├── __init__.py
│   │   ├── pool.py               # asyncpg pool helpers
│   │   ├── users.py
│   │   ├── conversations.py
│   │   ├── messages.py
│   │   ├── keywords.py
│   │   ├── scenarios.py
│   │   └── events_log.py
│   │
│   └── utils/
│       ├── __init__.py
│       ├── logging.py
│       ├── short_id.py           # nanoid с алфавитом без `_`
│       ├── deep_link.py          # build_telegram_deep_link()
│       └── time.py               # UTC helpers
│
├── migrations/                   # Чистый SQL — как в bot_purify
│   ├── 001_init.sql
│   ├── 002_keywords_scenarios.sql
│   ├── 003_dedup.sql
│   └── ...
│
├── tests/
│   ├── conftest.py
│   ├── test_provider_sendpulse.py
│   ├── test_keyword_matcher.py
│   ├── test_scenario_engine.py
│   ├── test_safety_filters.py
│   ├── test_lead_endpoint.py
│   └── test_short_id.py
│
├── docker/
│   ├── Dockerfile
│   └── Dockerfile.worker
│
├── docs/
│   ├── api_endpoints.md
│   ├── sendpulse_setup.md        # Как Юля настраивает SendPulse
│   └── tasks/
│       ├── task01_setup.md
│       ├── task02_sendpulse_signup.md
│       ├── task03_db_schema.md
│       └── ...
│
├── docker-compose.yml            # api + worker + postgres + redis (или подключение к существующему compose)
├── .env.example
├── .env                          # Не коммитить!
├── .gitignore
├── .pre-commit-config.yaml
├── pyproject.toml
├── Makefile
├── README.md
└── CLAUDE.md                     # Этот файл
```

---

## 7. MessagingProvider — интерфейс

### 7.1. Базовый интерфейс

`app/providers/base.py` — содержит только `MessagingProvider` ABC.

`app/models/events.py` — содержит `IncomingEvent` и `OutgoingMessage` как
**Pydantic v2 модели** (изменено vs v1: было `@dataclass(frozen=True)`).

Причины перехода на Pydantic:
- Модели сериализуются в JSON для arq queue (через Redis)
- Runtime-валидация литералов (platform, direction, event_type)
- Pydantic v2 даёт frozen-семантику через `model_config = ConfigDict(frozen=True)`
- Единый паттерн с остальной кодовой базой проекта

Полные определения см. в реальных файлах. Краткая структура:

- **IncomingEvent** — нормализованное входящее событие. Поля: provider, platform,
  event_type, external_user_id, external_event_id, username/full_name (опц.),
  text/media_url, post_id/comment_id (для comment-event), occurred_at, raw_payload.
- **OutgoingMessage** — исходящее сообщение. Поля: platform, external_user_id,
  text/media_url, quick_replies (list[QuickReply]), reply_to_comment_id, scenario_id.
- **QuickReply** — кнопка под сообщением. Поля: title (max 20), payload.
- **MessagingProvider** — ABC с методами parse_webhook, send, fetch_user_profile.

### 7.2. SendPulseProvider

`app/providers/sendpulse.py` реализует абстракцию через SendPulse REST API:

- **Auth:** OAuth2 client_credentials (client_id + client_secret), токен кэшируется в Redis с TTL
- **Webhook signature:** SendPulse подписывает webhooks через HMAC, валидируем
- **Rate limits:** SendPulse даёт 5 req/sec на API — учитываем в `rate_limiter.py`
- **Endpoints (актуальные на апрель 2026):**
  - `POST /chatbots/messengers/instagram/contacts/{contact_id}/messages` — отправка
  - `POST /chatbots/messengers/facebook/contacts/{contact_id}/messages` — отправка
  - GET `/chatbots/messengers/instagram/contacts/{contact_id}` — профиль

**При смене SendPulse → Manychat:** пишется `app/providers/manychat.py`, в `app/config.py` меняется `MESSAGING_PROVIDER=manychat`, в DI поднимается другой класс. Логика сценариев не меняется.

---

## 8. Схема БД

**Все timestamps — `TIMESTAMPTZ` в UTC. PK — `BIGSERIAL` или `UUID`.**

База данных **отдельная** от bot_purify (разные contained_db в одном Postgres-инстансе ИЛИ разные Postgres-контейнеры). См. § 14.

### 8.1. social_users

```sql
CREATE TABLE social_users (
    id              BIGSERIAL PRIMARY KEY,
    platform        TEXT NOT NULL CHECK (platform IN ('instagram', 'facebook')),
    external_id     TEXT NOT NULL,         -- IGSID/PSID или SendPulse contact_id (зависит от провайдера)
    provider_name   TEXT NOT NULL,         -- 'sendpulse' / 'manychat' / 'meta' — для миграции
    username        TEXT,
    full_name       TEXT,
    profile_pic_url TEXT,
    short_id        TEXT UNIQUE NOT NULL,  -- nanoid 8 символов, URL-safe без `_`
    first_seen_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_message_at TIMESTAMPTZ,
    smart_mode_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    tg_handover_at  TIMESTAMPTZ,
    tg_user_id      BIGINT,                -- Telegram user ID (заполняется bot_purify через callback)
    deleted_at      TIMESTAMPTZ,           -- soft delete для GDPR
    metadata        JSONB NOT NULL DEFAULT '{}',
    UNIQUE (provider_name, platform, external_id)
);

CREATE INDEX idx_social_users_short_id ON social_users(short_id);
CREATE INDEX idx_social_users_last_message ON social_users(last_message_at DESC) WHERE deleted_at IS NULL;
```

**Важно про short_id:** nanoid 8 символов, алфавит `0-9 A-Z a-z` (без `_` и `-` — у нас `_` это разделитель в deep-link, а `-` некрасиво для пользователей). Коллизии при 8 символах из 62-символьного алфавита — пренебрежимо малы для нашего масштаба (до 100k лидов в горизонте 5 лет).

### 8.2. conversations

```sql
CREATE TABLE conversations (
    id              BIGSERIAL PRIMARY KEY,
    user_id         BIGINT NOT NULL REFERENCES social_users(id) ON DELETE CASCADE,
    platform        TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'active'
                    CHECK (status IN ('active', 'closed', 'handover_pending', 'handover_done')),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_message_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    closed_at       TIMESTAMPTZ,
    handover_reason TEXT
);

CREATE INDEX idx_conversations_user ON conversations(user_id);
CREATE INDEX idx_conversations_status ON conversations(status) WHERE status != 'closed';
```

### 8.3. messages

```sql
CREATE TABLE messages (
    id                  BIGSERIAL PRIMARY KEY,
    conversation_id     BIGINT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    direction           TEXT NOT NULL CHECK (direction IN ('in', 'out')),
    text                TEXT,
    media_url           TEXT,
    media_type          TEXT,
    source              TEXT,                  -- dm, comment, comment_private_reply
    scenario_id         BIGINT REFERENCES scenarios(id),
    claude_used         BOOLEAN NOT NULL DEFAULT FALSE,
    claude_model        TEXT,
    claude_tokens_in    INTEGER,
    claude_tokens_out   INTEGER,
    safety_blocked      BOOLEAN NOT NULL DEFAULT FALSE,  -- если ответ был заблокирован safety-фильтром
    safety_reason       TEXT,
    external_message_id TEXT,                  -- для idempotency
    raw_payload         JSONB,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (external_message_id)
);

CREATE INDEX idx_messages_conversation ON messages(conversation_id, created_at);
CREATE INDEX idx_messages_created ON messages(created_at DESC);
```

### 8.4. scenarios, keywords, comment_triggers, events_log, comment_replies_dedup

Аналогично v1 (см. историю CLAUDE.md). Поля идентичны.

### 8.5. Retention

- `messages` — 90 дней, потом cron удаляет
- `events_log` — 30 дней
- `social_users`, `conversations` — постоянно, до явного запроса на удаление

---

## 9. API контракты

### 9.1. Webhook от SendPulse

**`POST /webhooks/sendpulse`**

Алгоритм:
1. Прочитать raw body
2. Валидация SendPulse signature (механизм — см. документацию SendPulse, реализуется в `SendPulseProvider.parse_webhook`)
3. При невалидной подписи — 200 OK + лог `signature_valid=False` (не 403, чтобы провайдер не пометил endpoint как broken)
4. Парсинг через `SendPulseProvider.parse_webhook` → список `IncomingEvent`
5. Запись в `events_log`
6. enqueue в arq для каждого события
7. **Возврат 200 OK немедленно** (<1 сек total)

### 9.2. Lead context для bot_purify

**`GET /api/lead/{short_id}`**

Auth: shared secret в заголовке `X-Internal-Token`.

**Response 200:**
```json
{
  "user": {
    "platform": "instagram",
    "username": "masha_p",
    "full_name": "Маша П.",
    "first_seen_at": "2026-04-30T10:23:00Z"
  },
  "scenario": "purify",
  "recent_messages": [
    {"direction": "in",  "text": "Хочу узнать про очищение",   "created_at": "2026-04-30T10:23:00Z"},
    {"direction": "out", "text": "Привет! Переходи в Telegram...", "created_at": "2026-04-30T10:23:05Z"}
  ]
}
```

**Response 401** — неверный `X-Internal-Token`
**Response 404** — `short_id` не найден или просрочен (срок жизни — 30 дней с момента последнего сообщения)

После успешного запроса — ОПЦИОНАЛЬНО bot_purify может вызвать **`POST /api/lead/{short_id}/handover`** с `{"tg_user_id": 123456}` чтобы записать факт перехода. Это даёт нам метрики conversion. Реализовать в `bot_purify` отдельной задачей после MVP.

### 9.3. Admin API

`/api/admin/*` — CRUD для scenarios, keywords, comment_triggers + просмотр диалогов + ручной handover.
Auth: Basic Auth на этапе MVP.

### 9.4. Internal callback из SendPulse-flow в n8n

**Опционально**, не обязательно для MVP. Если в будущем захочется выгружать данные в Google Sheets для Юли — n8n из docker-compose забирает из Postgres readonly.

---

## 10. Интеграция с bot_purify

### 10.1. Поток

```
1. Пользователь @masha_p пишет в IG DM «Хочу узнать про очищение»
   или ставит «ОЧИЩЕНИЕ» в комментарий под Reels
                ↓
2. SendPulse отправляет нам webhook
                ↓
3. social_inbox создаёт social_users (если новый), conversations, messages
                ↓
4. ScenarioEngine видит keyword "очищение" → scenario "purify"
                ↓
5. lead_tracker генерирует short_id = "Kd7nQ2x9", сохраняет в БД
                ↓
6. ScenarioEngine формирует welcome-ответ:
   "🌿 Привет! Переходи в Telegram, там удобнее:
    https://t.me/yuliya_purify_bot?start=ig_Kd7nQ2x9_purify"
                ↓
7. SendPulseProvider.send() → SendPulse API → IG DM
                ↓
8. Пользователь жмёт ссылку → Telegram открывает yuliya_purify_bot
                ↓
9. bot_purify.start.cmd_start() видит payload "ig_Kd7nQ2x9_purify"
                ↓
10. bot_purify делает GET http://social-inbox-api:8000/api/lead/Kd7nQ2x9
    с X-Internal-Token
                ↓
11. social_inbox возвращает {user, scenario, recent_messages}
                ↓
12. bot_purify создаёт user с source="ig:Kd7nQ2x9:purify",
    social_username="masha_p", social_scenario="purify"
                ↓
13. bot_purify шлёт персонализированное приветствие + запускает quiz
                ↓
14. (опционально) bot_purify шлёт POST /api/lead/Kd7nQ2x9/handover
    с {"tg_user_id": 123456}
                ↓
15. social_inbox обновляет social_users.tg_handover_at, tg_user_id
```

### 10.2. Что уже сделано в bot_purify

После применения `TASK_social_inbox_integration.md`:

- ✅ `bot/handlers/start.py` понимает payload `ig_<short_id>_<scenario>`
- ✅ `bot/services/social_inbox.py` — клиент к нашему API с timeout 3 сек
- ✅ `bot/services/db.py:create_user` принимает social-поля
- ✅ Миграция 006 добавляет колонки `social_platform`, `social_short_id`, `social_username`, `social_scenario`
- ✅ Google Sheets mirror автоматически экспортирует новые поля
- ✅ Если social_inbox недоступен — fallback на стандартный quiz без потерь

### 10.3. Сетевая связь

В docker-compose `bot_purify` и `social_inbox` живут в одной сети (или стыкуются через external network). bot_purify обращается к социальному инбоксу по имени контейнера: `http://social-inbox-api:8000`.

Снаружи (для SendPulse webhooks) social_inbox доступен через Traefik/nginx по публичному URL: `https://inbox.your-domain.com/webhooks/sendpulse`.

### 10.4. Shared secret

`SOCIAL_INBOX_API_TOKEN` — длинная случайная строка, генерируется один раз. Прописана:
- В `.env` сервиса `social_inbox` (как `INTERNAL_API_TOKEN`)
- В `.env` сервиса `bot_purify` (как `SOCIAL_INBOX_API_TOKEN`)

**Менять при компрометации** — простая операция, оба сервиса перезапустить.

### 10.5. Формат deep-link

Зафиксированный формат deep-link (используется `lead_tracker.build_deep_link`):

    https://t.me/{telegram_bot_username}?start=ig_{short_id}_{scenario_slug}

Правила:
- `short_id` — 8 chars `[0-9A-Za-z]`, без `_` и `-` (см. `app/utils/short_id.py`)
- `scenario_slug` — lowercase ASCII `[a-z0-9-]+`, без `_` и пробелов
- bot_purify парсит payload по первому `_` после префикса `ig_` →
  `(short_id, scenario_slug)`. См. `bot_purify/handlers/start.py:_parse_deep_link`.
- Известные слаги: `purify`, `oils`, `faq`. Новые слаги добавляются по согласованию
  с командой bot_purify (могут понадобиться отдельные приветствия).

### 10.6. lead_tracker сервис

`app/services/lead_tracker.py` — единая точка работы с переходом «social → Telegram»:

- `build_deep_link(short_id, slug)` — формирует URL по правилам §10.5
- `was_welcome_sent(user_id)` / `mark_welcome_sent(user_id)` — Redis-флаги
  lifetime-idempotency для welcome (TTL 180 дней)
- `record_handover(user_id, tg_user_id)` — отметка успешного перехода
  (вызывается из endpoint'а в Task 11)

---

## 11. Правила кода

### 11.1. Общее

- **Английский** для имён функций, переменных, классов, комментариев, docstring, логов, commit messages, имён файлов
- **Русский** только в: бизнес-контенте (тексты scenarios.template, system prompts, тексты приветствий), комментариях про специфическую doTERRA-логику
- **Файлы:** UTF-8 без BOM, LF окончания (мы на Linux в проде; bot_purify тоже через Docker Linux)
- **Размер файла:** не больше 500 строк
- **Размер функции:** не больше 50 строк

### 11.2. Async/sync

- Всё IO — async
- Никаких блокирующих вызовов в async-функциях
- CPU-нагрузка — в `asyncio.to_thread()`

### 11.3. Type hints

- 100% type hints на публичных функциях и методах
- Pydantic v2 для всех структур данных, проходящих через границы
- `mypy --strict` должен проходить

### 11.4. Логирование

- structlog с JSON в проде
- Никаких `print()` в production-коде
- В каждый лог — `request_id` или `event_id` для корреляции
- НЕ логировать содержимое сообщений в INFO (только в DEBUG, только в dev)
- PII-маскирование как в bot_purify/audit.py — phone и email маскируются перед логом

### 11.5. Обработка ошибок

- Не использовать голый `except:` или `except Exception:` без логирования
- Webhook endpoint — ловит всё и возвращает 200
- Воркер — пусть падает, arq делает retry с экспоненциальной задержкой

### 11.6. Конфигурация

- Всё через `pydantic-settings`, `app/config.py`
- НЕ читать env через `os.getenv` напрямую в коде
- `.env.example` всегда обновлять

### 11.7. Зависимости

- Добавление новой — только если реально нужна
- Зафиксировать версию в `pyproject.toml` + `uv lock`

### 11.8. Миграции

- Любое изменение схемы — через миграцию (raw SQL, как в bot_purify)
- Имя: `NNN_<описание>.sql` (001, 002, 003)
- Никаких ручных ALTER в проде

---

## 12. Безопасность

### 12.1. Webhook signature

Реализуется внутри `SendPulseProvider.parse_webhook()`. Возвращает пустой список (без raise) при невалидной подписи. Endpoint `/webhooks/sendpulse` всегда возвращает 200.

### 12.2. Секреты

В `.env`, НЕ в репо:
- `SENDPULSE_CLIENT_ID`
- `SENDPULSE_CLIENT_SECRET`
- `SENDPULSE_WEBHOOK_SECRET`
- `ANTHROPIC_API_KEY`
- `POSTGRES_PASSWORD`
- `INTERNAL_API_TOKEN` (shared с bot_purify)
- `ADMIN_BASIC_AUTH_PASSWORD`
- `SENTRY_DSN`

### 12.3. Защита от prompt injection

Сообщения пользователя идут в Claude API:
- System prompt чётко определяет роль и границы
- User-сообщение оборачиваем в XML: `<user_message>...</user_message>`
- Пост-фильтр ответов Claude через `safety.py` — см. § 13

### 12.4. Rate limiting

- Per-user: не больше 10 ответов в день одному пользователю
- Per-keyword-trigger: не больше 1 раза в 30 дней одному пользователю на один scenario
- Welcome: ровно 1 раз на пользователя
- SendPulse API: 5 req/sec — backoff с jitter

### 12.5. Idempotency

- `external_message_id` UNIQUE в `messages`
- `comment_id` в `comment_replies_dedup`
- `external_event_id` в `events_log` — UNIQUE INDEX

---

## 13. Safety и compliance для контента doTERRA

**Это самое важное требование проекта с юридической точки зрения.**

### 13.1. Запрещённые формулировки

`app/prompts/banned_patterns.py`:

```python
BANNED_PATTERNS = [
    # Медицинские заявления
    r"\bлечит\b", r"\bвылечит\b", r"\bизлечив\w*\b",
    r"\bпрофилактик\w* (рак|covid|гриппа|онкологии)",
    r"\bантибиотик",
    r"\bдиагноз",
    r"\bвместо лекарств",
    # Категорические обещания
    r"\bгарантирую\b", r"\b100% результат\b",
    # Опасное самолечение
    r"внутрь без консультации",
    r"отмен\w* (лекарств|препарат)",
]
```

### 13.2. Pipeline проверки

Каждое исходящее сообщение перед отправкой проходит:

1. `safety.check_banned_patterns(text)` — regex по списку выше
2. Если триггерит — НЕ отправлять, ставить статус conversation = `handover_pending`, уведомлять Юлю в Telegram
3. Логировать в `messages.safety_blocked=true, safety_reason="banned: лечит"`

### 13.3. Human agent escalation

Триггеры эскалации:
- Пользователь пишет: «оператор», «человек», «human», «agent», «администратор»
- Сообщение содержит вопрос о медицинском состоянии (keyword list: «болит», «диагноз», «врач», «больница»)
- Claude вернул `tool_use` с `escalate_to_human`
- Сообщение не понято за 3 итерации
- Сработал safety-фильтр (см. 13.2)

### 13.4. Disclaimer

Welcome-сообщение **всегда** содержит:
> «Это автоматический помощник Юлии 🤖 Для личной консультации напиши "оператор"»

---

## 14. Деплой и инфраструктура

### 14.1. Расположение

`D:\Work\social_inbox` — отдельный git-репо. **НЕ** mono-repo с bot_purify.

### 14.2. Docker Compose стратегия

**Вариант А (рекомендуется на старте):** отдельный `docker-compose.yml` в `social_inbox`, со своими postgres и redis. На VPS поднимаем оба compose с подключением к общей external network для связи api между собой:

```yaml
# docker-compose.yml в social_inbox
networks:
  default:
    name: purify-shared
    external: true
```

```yaml
# docker-compose.override.yml в bot_purify (на проде)
networks:
  default:
    name: purify-shared
    external: true
```

Тогда из bot_purify контейнера можно ходить на `http://social-inbox-api:8000` напрямую.

**Вариант Б (на будущее):** объединить в один compose-файл когда оба проекта стабилизируются.

### 14.3. Сервисы в docker-compose.yml social_inbox

```yaml
services:
  api:                # FastAPI на 8000
  worker:             # arq worker
  postgres:           # отдельный инстанс, не общий с bot_purify
  redis:              # отдельный инстанс, не общий с bot_purify
  # n8n опционально, см. § 14.4
```

### 14.4. n8n (опционально)

Не для MVP. Добавлять когда понадобится:
- Marketing flows (отправка лидов в Google Sheets, Notion)
- Не-критичные интеграции

### 14.5. Reverse proxy

Traefik или nginx (в зависимости от того, что у тебя на VPS уже стоит) для:
- HTTPS на webhook endpoint (`https://inbox.<your-domain>/webhooks/sendpulse`)
- HTTPS на admin (`https://inbox.<your-domain>/admin`)
- API endpoint `/api/lead/...` НЕ светим наружу (только внутри docker network)

### 14.6. Production rollout

1. Deploy social_inbox без активации SendPulse webhook (smoke test)
2. Применить TASK_social_inbox_integration.md в bot_purify, убедиться что fallback работает
3. Подключить SendPulse к одному Reels Юлии с keyword «ОЧИЩЕНИЕ»
4. Мониторить 3-5 дней
5. Расширять на остальной контент

### 14.7. Production runbook

Подробное руководство по развёртыванию, обновлению, бэкапам и восстановлению:
`deploy/README.md`.

Ключевые команды:
- Deploy новой версии: `cd /opt/social_inbox && ./deploy/scripts/deploy.sh`
- Smoke check: `./deploy/scripts/smoke_check.sh`
- Env check: `./deploy/scripts/env_check.sh`
- Backup: `./deploy/backup/backup.sh`
- Restore: `./deploy/backup/restore.sh <dump.sql.gz>`

Production overlay:
`docker compose --env-file .env.compose -f docker-compose.yml -f deploy/docker-compose.prod.yml up -d`.
Требует docker compose v2.20+ (для `!override` тегов, очищающих base sequences).

Прод использует **два** env-файла, чтобы не конфликтовать с `Settings(extra="forbid")`:
- `.env` — app-переменные, загружаются в api/admin/worker через `env_file: .env`
- `.env.compose` — compose-substitution vars (POSTGRES_USER/PASSWORD/DB,
  PUBLIC_HOST_*, TRAEFIK_ACME_EMAIL), передаётся через `--env-file .env.compose`.

---

## 15. Работа с задачами

### 15.1. Структура задачи

Каждая задача — отдельный markdown в `docs/tasks/taskNN_name.md`. Формат как в `TASK_social_inbox_integration.md`:

```markdown
# Task NN: <название>

## Контекст
## Цель
## Подзадачи (1, 2, 3 с подпунктами a/b/c)
## Acceptance criteria (чек-лист)
## Do NOT
## Зависимости
```

### 15.2. Правила выполнения для Claude Code

- Работать по ОДНОЙ задаче за раз
- В начале сессии: прочитать CLAUDE.md → прочитать задачу → задать уточняющие вопросы Виктору ДО написания кода
- НЕ переходить к следующей задаче без подтверждения
- При завершении: коммит + краткий отчёт что сделано / что нет / открытые вопросы

### 15.3. Запрещённые действия для Claude Code

- НЕ изменять файлы вне scope задачи без явного разрешения
- НЕ обновлять зависимости «заодно»
- НЕ переименовывать существующие сущности БД без миграции
- НЕ коммитить `.env`, `*.log`, `__pycache__`, `.venv/`
- НЕ деплоить — только Виктор делает деплой

---

## 16. Команды Makefile

```makefile
.PHONY: install up down logs lint test migrate

install:
	uv sync

up:
	docker compose up -d

down:
	docker compose down

logs:
	docker compose logs -f api worker

lint:
	uv run ruff check app/ tests/
	uv run mypy app/

format:
	uv run ruff format app/ tests/

test:
	uv run pytest tests/ -v

migrate:
	docker compose exec api python -c "from app.repos.pool import run_migrations; import asyncio; asyncio.run(run_migrations())"

shell:
	docker compose exec api python
```

---

## 17. Roadmap

| # | Task | Статус | Срок |
|---|------|--------|------|
| 01 | Project setup (uv, ruff, mypy, docker-compose, .env.example) | 📋 To do | 1 день |
| 02 | SendPulse регистрация + подключение IG/FB Юлии | 📋 To do (сделает Виктор) | 1 день |
| 03 | DB schema + raw SQL migrations | 📋 To do | 1 день |
| 04 | MessagingProvider interface + IncomingEvent/OutgoingMessage | 📋 To do | 1 день |
| 05 | SendPulseProvider — parse_webhook + send | 📋 To do | 2 дня |
| 06 | Webhook endpoint + arq scaffold | 📋 To do | 1 день |
| 07 | ScenarioEngine + KeywordMatcher | 📋 To do | 2 дня |
| 08 | Welcome scenario + first DM detection | 📋 To do | 1 день |
| 09 | Comment-to-DM scenario | 📋 To do | 2 дня |
| 10 | lead_tracker + short_id + deep link | 📋 To do | 1 день |
| 11 | `/api/lead/{short_id}` endpoint | 📋 To do | 1 день |
| 12 | bot_purify integration ([отдельная задача](TASK_social_inbox_integration.md)) | 📋 Готова, ожидает применения | 0.5 дня |
| 13 | ClaudeResponder + tool use | 📋 To do | 2 дня |
| 14 | Safety filters + human agent escalation | 📋 To do | 1 день |
| 15 | Admin dashboard (минимальный) | 📋 To do | 2 дня |
| 16 | Monitoring (Sentry, healthcheck) | 📋 To do | 1 день |
| 17 | Production deployment + reverse proxy | ✅ Done (2026-05-16) | 1 день |
| 18 | Smoke tests + go-live | 📋 To do | 0.5 дня |

**Итого: ~20 человеко-дней разработки.** При работе по 1.5–2 часа в день — **~3 недели до MVP.**

---

## 18. Открытые вопросы (требуют решения до начала разработки)

1. **VPS:** где деплоим? Тот же, где `bot_purify`? Если да — какой провайдер, IP, доступы?
2. **Домен:** какой URL для webhook? `inbox.<your-domain>` существующий или отдельный домен?
3. **Reverse proxy:** что уже стоит на VPS (Traefik/nginx/caddy)?
4. **SendPulse Account:** на чей email регистрируем? (отложил решение, см. § 6 в обсуждении)
5. **Sentry:** использовать общий проект Sentry для bot_purify + social_inbox или разные?

---

## 19. Что меняется vs CLAUDE.md v1

Краткий changelog для понимания эволюции архитектуры:

- **Меняется:** провайдер с Meta-direct на SendPulse Free (нет юрлица сейчас)
- **Добавляется:** MessagingProvider абстракция для безболезненной миграции в будущем
- **Меняется:** Webhook signature теперь от SendPulse, не от Meta
- **Добавляется:** конкретное описание интеграции с реальным bot_purify (видел код)
- **Конкретизируется:** short_id формат (nanoid 8 chars без `_`)
- **Удаляется:** Meta App Review задачи (на этом этапе не нужны)
- **Сохраняется без изменений:** схема БД, ScenarioEngine, ClaudeResponder, safety-фильтры, structlog, asyncpg-стек, docker-compose подход
- **Уточняется (Task 04):** IncomingEvent и OutgoingMessage — Pydantic v2 модели
  (было: dataclass). Детали в § 7.1.

---

**Последнее обновление:** 2026-05-16 (Task 17: production deployment — deploy/ runbook, prod compose overlay, Traefik+ACME, backup scripts)
**Поддерживается:** Виктор + Claude
