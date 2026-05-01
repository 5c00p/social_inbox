# Task 01: Project setup

> Применить в **новом пустом каталоге** `D:\Work\social_inbox`. Открыть в VS Code, затем попросить Claude Code: «Прочитай TASK_01_setup.md и выполни все подзадачи последовательно. Перед каждой подзадачей кратко опиши план, после — что фактически сделано».

---

## Контекст

Создаём с нуля проект `social_inbox` — сервис автоматизации Instagram/Facebook DM для блога Юлии (см. CLAUDE.md в корне проекта).

Это **первая задача** в проекте. После её выполнения должен быть рабочий скелет: структура каталогов, зависимости, FastAPI запускается через docker compose с заглушечным `/health` эндпоинтом, тесты проходят, lint чистый.

**Никакой бизнес-логики в этой задаче нет.** Она только подготавливает каркас. Логика (провайдеры, БД, сценарии) — в следующих задачах.

---

## Цель

После выполнения этой задачи:

- Существует структура каталогов проекта
- `uv sync` устанавливает все зависимости без ошибок
- `make up` поднимает docker-compose с FastAPI + Postgres + Redis (без миграций — те в Task 03)
- `curl http://localhost:8000/health` возвращает `{"status": "ok"}`
- `make lint` (ruff + mypy) проходит без ошибок
- `make test` проходит на смоук-тестах
- Pre-commit hooks работают
- `.env.example` содержит все нужные переменные с плейсхолдерами

---

## Подзадачи

### 1. Структура каталогов

a) Создать в `D:\Work\social_inbox` следующую структуру (пустые `__init__.py` где нужно):

```
social_inbox/
├── app/
│   ├── __init__.py
│   ├── main.py
│   ├── config.py
│   ├── deps.py
│   ├── api/
│   │   ├── __init__.py
│   │   └── health.py
│   ├── providers/
│   │   ├── __init__.py
│   │   └── base.py              # пустой, реализуется в Task 04
│   ├── models/
│   │   ├── __init__.py
│   │   └── enums.py
│   ├── services/
│   │   └── __init__.py
│   ├── workers/
│   │   └── __init__.py
│   ├── prompts/
│   │   └── __init__.py
│   ├── repos/
│   │   └── __init__.py
│   └── utils/
│       ├── __init__.py
│       └── logging.py
├── tests/
│   ├── __init__.py
│   ├── conftest.py
│   └── test_health.py
├── docker/
│   └── Dockerfile
├── docs/
│   └── tasks/
├── migrations/                  # пустой, наполняется в Task 03
├── .env.example
├── .gitignore
├── .pre-commit-config.yaml
├── docker-compose.yml
├── Makefile
├── pyproject.toml
├── README.md
└── CLAUDE.md                    # уже есть, не трогать
```

b) `__init__.py` файлы создавать пустыми.

### 2. pyproject.toml и зависимости

a) Создать `pyproject.toml`:

```toml
[project]
name = "social-inbox"
version = "0.1.0"
description = "Automated messaging service for Instagram/Facebook lead capture"
requires-python = ">=3.12,<3.13"
dependencies = [
    "fastapi==0.115.6",
    "uvicorn[standard]==0.34.0",
    "httpx==0.28.1",
    "pydantic==2.10.4",
    "pydantic-settings==2.7.1",
    "asyncpg==0.31.0",
    "redis==5.2.1",
    "arq==0.26.3",
    "anthropic==0.42.0",
    "structlog==24.4.0",
    "sentry-sdk[fastapi]==2.19.2",
    "nanoid==2.0.0",
    "python-dotenv==1.0.1",
]

[dependency-groups]
dev = [
    "pytest==8.3.4",
    "pytest-asyncio==0.25.0",
    "pytest-cov==6.0.0",
    "ruff==0.8.4",
    "mypy==1.13.0",
    "pre-commit==4.0.1",
    "httpx==0.28.1",  # для тестов через TestClient
]

[tool.ruff]
line-length = 100
target-version = "py312"

[tool.ruff.lint]
select = [
    "E", "W",      # pycodestyle
    "F",           # pyflakes
    "I",           # isort
    "B",           # flake8-bugbear
    "UP",          # pyupgrade
    "N",           # pep8-naming
    "C4",          # comprehensions
    "SIM",         # simplify
    "RUF",         # ruff-specific
]
ignore = [
    "E501",        # line too long (handled by formatter)
    "B008",        # function call in default argument (FastAPI Depends)
    "RUF001",      # ambiguous unicode (Cyrillic in business text is intentional)
    "RUF002",
    "RUF003",
]

[tool.ruff.format]
quote-style = "double"

[tool.mypy]
python_version = "3.12"
strict = true
warn_return_any = true
warn_unused_ignores = true
plugins = ["pydantic.mypy"]

[[tool.mypy.overrides]]
module = ["arq.*", "nanoid.*"]
ignore_missing_imports = true

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
python_files = ["test_*.py"]
addopts = "-v --tb=short"

[tool.coverage.run]
source = ["app"]
omit = ["*/tests/*", "*/migrations/*"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["app"]
```

b) Запустить `uv sync` для установки зависимостей. Это создаст `.venv/` и `uv.lock`.

c) Зафиксировать `uv.lock` в репо (это правильно для приложений, не для библиотек).

### 3. Конфигурация (config.py)

a) Создать `app/config.py`:

```python
"""Application configuration loaded from environment."""
from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Centralised configuration. All env vars MUST be defined here, never read via os.getenv."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="forbid",
    )

    # --- Environment ---
    env: Literal["dev", "prod"] = "dev"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    # --- Database ---
    postgres_dsn: str = Field(..., description="postgresql://user:pass@host:port/db")

    # --- Redis ---
    redis_url: str = Field(..., description="redis://host:port/db")

    # --- Messaging provider (active) ---
    messaging_provider: Literal["sendpulse", "manychat", "meta"] = "sendpulse"

    # --- SendPulse credentials (used when messaging_provider='sendpulse') ---
    sendpulse_client_id: str = ""
    sendpulse_client_secret: str = ""
    sendpulse_webhook_secret: str = ""

    # --- Anthropic ---
    anthropic_api_key: str = ""
    claude_default_model: str = "claude-sonnet-4-6"

    # --- Internal API (shared with bot_purify) ---
    internal_api_token: str = Field(..., description="Shared secret with bot_purify")

    # --- Admin ---
    admin_basic_auth_user: str = "admin"
    admin_basic_auth_password: str = ""

    # --- Sentry ---
    sentry_dsn: str = ""

    # --- App URLs ---
    public_base_url: str = "http://localhost:8000"
    telegram_bot_username: str = "yuliya_purify_bot"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
```

b) Создать `.env.example` с плейсхолдерами:

```bash
# --- Environment ---
ENV=dev
LOG_LEVEL=INFO

# --- Database ---
POSTGRES_DSN=postgresql://social_inbox:social_inbox@postgres:5432/social_inbox

# --- Redis ---
REDIS_URL=redis://redis:6379/0

# --- Messaging provider (active) ---
MESSAGING_PROVIDER=sendpulse

# --- SendPulse credentials ---
SENDPULSE_CLIENT_ID=
SENDPULSE_CLIENT_SECRET=
SENDPULSE_WEBHOOK_SECRET=

# --- Anthropic ---
ANTHROPIC_API_KEY=
CLAUDE_DEFAULT_MODEL=claude-sonnet-4-6

# --- Internal API (shared with bot_purify) ---
INTERNAL_API_TOKEN=replace-me-with-long-random-string

# --- Admin ---
ADMIN_BASIC_AUTH_USER=admin
ADMIN_BASIC_AUTH_PASSWORD=replace-me

# --- Sentry ---
SENTRY_DSN=

# --- App URLs ---
PUBLIC_BASE_URL=http://localhost:8000
TELEGRAM_BOT_USERNAME=yuliya_purify_bot

# --- Postgres container env ---
POSTGRES_USER=social_inbox
POSTGRES_PASSWORD=social_inbox
POSTGRES_DB=social_inbox
```

c) Создать `.env` копированием `.env.example`. **Не коммитить!**

### 4. Логирование (utils/logging.py)

a) Создать `app/utils/logging.py`:

```python
"""Structured logging via structlog.

Use:
    from app.utils.logging import get_logger
    log = get_logger(__name__)
    log.info("event", user_id=42, source="ig")
"""
from __future__ import annotations

import logging
import sys
from typing import Any

import structlog

from app.config import get_settings


def _configure_structlog(json_output: bool, level: str) -> None:
    timestamper = structlog.processors.TimeStamper(fmt="iso", utc=True)
    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        timestamper,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    if json_output:
        renderer: Any = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=True)

    structlog.configure(
        processors=[*shared_processors, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(getattr(logging, level)),
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=getattr(logging, level),
    )


def setup_logging() -> None:
    """Initialise logging based on settings. Call once at app startup."""
    settings = get_settings()
    _configure_structlog(
        json_output=(settings.env == "prod"),
        level=settings.log_level,
    )


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)
```

### 5. Health endpoint (api/health.py)

a) Создать `app/api/health.py`:

```python
"""Health and readiness endpoints."""
from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict[str, str]:
    """Liveness probe — always returns ok if process is up."""
    return {"status": "ok"}


@router.get("/ready")
async def ready() -> dict[str, str]:
    """Readiness probe — for now identical to /health.

    Will be extended in Task 03 to check Postgres + Redis connectivity.
    """
    return {"status": "ready"}
```

### 6. Главный модуль (main.py)

a) Создать `app/main.py`:

```python
"""FastAPI application entry point."""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI

from app.api import health
from app.config import get_settings
from app.utils.logging import get_logger, setup_logging


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    setup_logging()
    log = get_logger(__name__)
    settings = get_settings()
    log.info("startup", env=settings.env, provider=settings.messaging_provider)
    yield
    log.info("shutdown")


def create_app() -> FastAPI:
    app = FastAPI(
        title="social_inbox",
        version="0.1.0",
        description="Automated messaging service for Instagram/Facebook lead capture",
        lifespan=lifespan,
    )
    app.include_router(health.router)
    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
```

### 7. Enums (для следующих задач, фундамент)

a) Создать `app/models/enums.py`:

```python
"""Shared enum-like literals used across the project."""
from __future__ import annotations

from typing import Literal

Platform = Literal["instagram", "facebook"]
Direction = Literal["in", "out"]
ConversationStatus = Literal[
    "active",
    "closed",
    "handover_pending",
    "handover_done",
]
ScenarioType = Literal[
    "welcome",
    "comment_to_dm",
    "faq",
    "handover",
    "smart",
]
EventType = Literal["message", "comment", "postback"]
ProviderName = Literal["sendpulse", "manychat", "meta"]
```

### 8. Docker

a) Создать `docker/Dockerfile`:

```dockerfile
FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Install uv (fast Python package manager)
COPY --from=ghcr.io/astral-sh/uv:0.5.13 /uv /usr/local/bin/uv

# Copy dependency files first for layer caching
COPY pyproject.toml uv.lock ./

# Install dependencies into system Python (no .venv inside container)
RUN uv sync --frozen --no-dev --no-install-project

# Copy source
COPY app/ ./app/
COPY migrations/ ./migrations/

# Install the project itself
RUN uv sync --frozen --no-dev

EXPOSE 8000

CMD ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

b) Создать `docker-compose.yml`:

```yaml
name: social-inbox

services:
  api:
    build:
      context: .
      dockerfile: docker/Dockerfile
    restart: unless-stopped
    env_file: .env
    ports:
      - "8000:8000"
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy
    volumes:
      - ./app:/app/app
      - ./migrations:/app/migrations
    networks:
      - default

  worker:
    build:
      context: .
      dockerfile: docker/Dockerfile
    restart: unless-stopped
    env_file: .env
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy
    volumes:
      - ./app:/app/app
    # Worker command will be configured in Task 06 (arq worker scaffold)
    # For Task 01, worker is built but stays in idle to not crash.
    command: ["sleep", "infinity"]
    networks:
      - default

  postgres:
    image: postgres:16-alpine
    restart: unless-stopped
    environment:
      POSTGRES_USER: ${POSTGRES_USER:-social_inbox}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:-social_inbox}
      POSTGRES_DB: ${POSTGRES_DB:-social_inbox}
    volumes:
      - postgres_data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${POSTGRES_USER:-social_inbox}"]
      interval: 5s
      timeout: 5s
      retries: 5
    networks:
      - default

  redis:
    image: redis:7-alpine
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 5s
      retries: 5
    networks:
      - default

networks:
  default:
    name: social-inbox-default

volumes:
  postgres_data:
```

c) **Важно:** в этой задаче в `docker-compose.yml` ещё НЕ настраиваем external network `purify-shared` (как описано в § 14.2 CLAUDE.md). Это будет сделано в Task 17 (production deployment), потому что для локальной разработки это не нужно и усложняет setup.

### 9. Тесты (smoke)

a) Создать `tests/conftest.py`:

```python
"""Pytest fixtures."""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest.fixture
async def client() -> AsyncClient:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
```

b) Создать `tests/test_health.py`:

```python
"""Smoke tests for health endpoints."""
from __future__ import annotations

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_health_returns_ok(client: AsyncClient) -> None:
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_ready_returns_ok(client: AsyncClient) -> None:
    response = await client.get("/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ready"}
```

c) Конфигурация для тестов: создать `.env.test` (как `.env`, но с in-memory dummy значениями, чтобы pydantic-settings не падал на отсутствии POSTGRES_DSN):

```bash
ENV=dev
LOG_LEVEL=INFO
POSTGRES_DSN=postgresql://test:test@localhost:5432/test
REDIS_URL=redis://localhost:6379/0
MESSAGING_PROVIDER=sendpulse
INTERNAL_API_TOKEN=test-token
```

d) В `tests/conftest.py` добавить в начало (до импорта `app.main`):

```python
import os
os.environ["POSTGRES_DSN"] = "postgresql://test:test@localhost:5432/test"
os.environ["REDIS_URL"] = "redis://localhost:6379/0"
os.environ["INTERNAL_API_TOKEN"] = "test-token"
```

Это нужно, чтобы pydantic-settings не упал при импорте `app.main` в тестах. В Task 03 переедем на нормальную тестовую конфигурацию через `lru_cache.cache_clear()`.

### 10. Линт и pre-commit

a) Создать `.pre-commit-config.yaml`:

```yaml
repos:
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.8.4
    hooks:
      - id: ruff
        args: [--fix]
      - id: ruff-format

  - repo: https://github.com/pre-commit/pre-commit-hooks
    rev: v5.0.0
    hooks:
      - id: trailing-whitespace
      - id: end-of-file-fixer
      - id: check-yaml
      - id: check-added-large-files
        args: ["--maxkb=500"]
      - id: check-merge-conflict
      - id: detect-private-key
```

b) Установить hooks: `uv run pre-commit install`

### 11. Makefile

a) Создать `Makefile`:

```makefile
.PHONY: install up down logs lint format test migrate shell help

help:
	@echo "Available targets:"
	@echo "  install   - Install dependencies via uv"
	@echo "  up        - Start docker compose stack"
	@echo "  down      - Stop docker compose stack"
	@echo "  logs      - Tail logs from api and worker"
	@echo "  lint      - Run ruff and mypy"
	@echo "  format    - Auto-format code with ruff"
	@echo "  test      - Run pytest"
	@echo "  shell     - Open Python shell in api container"

install:
	uv sync

up:
	docker compose up -d --build

down:
	docker compose down

logs:
	docker compose logs -f api worker

lint:
	uv run ruff check app/ tests/
	uv run mypy app/

format:
	uv run ruff format app/ tests/
	uv run ruff check --fix app/ tests/

test:
	uv run pytest tests/

shell:
	docker compose exec api python
```

### 12. .gitignore

a) Создать `.gitignore`:

```
# Python
__pycache__/
*.py[cod]
*.egg-info/
.venv/
venv/

# Environment
.env
.env.local
.env.*.local

# Tests / coverage
.pytest_cache/
.coverage
htmlcov/
.mypy_cache/
.ruff_cache/

# IDE
.vscode/
.idea/
*.swp

# OS
.DS_Store
Thumbs.db

# Project
*.log
logs/
```

### 13. README.md

a) Создать минимальный `README.md`:

```markdown
# social_inbox

Automated messaging service for Instagram/Facebook lead capture.
Part of the Yulia Purify ecosystem (alongside bot_purify, purify-marathon).

See `CLAUDE.md` for full architectural specification.

## Quick start

```bash
cp .env.example .env
# edit .env — fill INTERNAL_API_TOKEN at minimum

make install      # uv sync
make up           # docker compose up -d
curl http://localhost:8000/health
```

## Development

```bash
make lint         # ruff + mypy
make format       # auto-format
make test         # pytest
make logs         # tail container logs
```

## Architecture

See `CLAUDE.md` § 4 for architecture diagram.
See `docs/tasks/` for the development roadmap.
```

---

## Acceptance criteria

После выполнения всех подзадач выполнить и поставить галочки:

- [ ] Структура каталогов соответствует пункту 1 (визуальная проверка через `tree -L 3` или эквивалент)
- [ ] `uv sync` отрабатывает без ошибок, создаётся `.venv/` и `uv.lock`
- [ ] `uv.lock` присутствует и закоммичен
- [ ] `cp .env.example .env` создаёт рабочий файл
- [ ] `make up` запускает 4 сервиса: api, worker, postgres, redis. Все становятся `healthy` (для api/worker — не падают)
- [ ] `docker compose ps` показывает api в состоянии running
- [ ] `curl http://localhost:8000/health` возвращает `{"status":"ok"}` (status code 200)
- [ ] `curl http://localhost:8000/ready` возвращает `{"status":"ready"}` (status code 200)
- [ ] `curl http://localhost:8000/docs` отдаёт Swagger UI
- [ ] `make lint` проходит без ошибок
- [ ] `make test` проходит, оба теста health зелёные
- [ ] `make down` корректно останавливает стек
- [ ] `pre-commit run --all-files` проходит без ошибок
- [ ] При импорте `from app.main import app` нет ImportError
- [ ] В логах при старте видна запись `event=startup env=dev provider=sendpulse`
- [ ] `.env` НЕ закоммичен (проверить `git status`)
- [ ] `.venv/` НЕ закоммичен

---

## Do NOT

- НЕ создавать Alembic-миграции в этой задаче. Миграции (raw SQL, как в bot_purify) — в Task 03.
- НЕ реализовывать MessagingProvider/SendPulseProvider — это Task 04 и Task 05. В этой задаче `app/providers/base.py` остаётся пустым с комментарием `# Implemented in Task 04`.
- НЕ настраивать external docker network `purify-shared` — это Task 17 (production deployment).
- НЕ подключать Sentry — переменная в config есть, но `sentry_sdk.init()` не вызывается в этой задаче.
- НЕ создавать БД-схему даже временную. Postgres контейнер просто стоит пустой.
- НЕ добавлять зависимости, не указанные в подзадаче 2.
- НЕ менять имя Python-пакета (должно быть `app/`, а не `social_inbox/`). Это упрощает импорты и совместимо с Docker WORKDIR.
- НЕ ставить worker в режим автоматического перезапуска arq — у нас в этой задаче только `sleep infinity`. Worker будет настроен в Task 06.
- НЕ коммитить `.env`, `.venv/`, `uv.lock`-как-removed.
- НЕ запускать `git init` если репо уже инициализировано. Если нет — спроси Виктора, делать ли init и какой remote использовать.

---

## Зависимости задачи

Эта задача — первая. Зависимостей от других задач нет.

После применения этой задачи можно начинать **Task 02** (SendPulse регистрация — это работа Виктора, не Claude Code) **параллельно с Task 03** (DB schema).

---

## Что после этой задачи

Следующие задачи в roadmap (см. CLAUDE.md § 17):

- **Task 02** — SendPulse регистрация и подключение IG/FB Юлии (делает Виктор вручную)
- **Task 03** — DB schema + raw SQL migrations (Claude Code)
- **Task 04** — MessagingProvider interface + IncomingEvent/OutgoingMessage (Claude Code)

---

**Дата создания:** 2026-04-30
**Применять в:** новый пустой каталог `D:\Work\social_inbox`
**Эстимейт:** 1–2 часа на Claude Code + ручная проверка
