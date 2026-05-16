# Task 18: Smoke tests + go-live checklist

> Применить в `D:\Work\social_inbox` после Tasks 01, 03, 04, 05, 06, 07, 08, 09, 11, 13, 14, 15, 16, 17.
>
> Это **финальная задача** в roadmap. После её применения проект формально готов к запуску с реальной аудиторией.

---

## Контекст

После Task 17 у нас развёрнутая на VPS production-система. Можно запустить webhook от SendPulse — он будет обработан. Можно открыть админку — Юля увидит интерфейс. Но **запустить на реальную аудиторию** — это отдельный шаг с риском:
- Что если SendPulse credentials в .env неправильные?
- Что если Anthropic API key утёк лимиты?
- Что если в проде Postgres не успел восстановиться после рестарта?
- Что если Юля не понимает что делать при первом handover?

Эта задача даёт **две вещи**:

1. **Smoke test script** — автоматизированная серия проверок против живого прода. Запускается перед go-live и в любой момент, когда сомневаешься в состоянии системы.

2. **Go-live checklist** — DOCX для Юли + Виктора с пошаговой процедурой запуска:
   - День -3: подготовка (DNS, credentials, бэкапы готовы)
   - День 0: canary rollout (один Reels, наблюдение)
   - День +1..+7: что смотреть, на что реагировать
   - Rollback procedure если что-то пошло не так

После применения Task 18 у Юли с Виктором есть **формальная процедура** «прошли чек-лист → запускаемся».

---

## Цель

После выполнения этой задачи:

- Существует `scripts/smoke_test.py` — Python CLI-скрипт, который запускает серию проверок против running прода
- Скрипт умеет запускать отдельные шаги (`--step webhook`) или всё подряд (`--all`)
- Каждая проверка имеет timeout и понятный exit code
- Готов DOCX `Go_Live_Checklist.docx` для Юли — манёвры запуска и emergency procedures
- Готов markdown `docs/go_live_runbook.md` — техническая копия для Виктора (deep-dive команды)
- `make smoke` запускает быстрый smoke check локально (на dev) или против production URL'а
- Все существующие тесты продолжают проходить
- В CLAUDE.md обновлён §17 — Task 18 отмечен как выполненный

---

## Подзадачи

### 1. Smoke test script

a) Создать `scripts/__init__.py` (пустой).

b) Создать `scripts/smoke_test.py`:

```python
"""End-to-end smoke checks against a live deployment.

Usage:
    # Run all checks against local stack
    uv run python scripts/smoke_test.py --base-url http://localhost:8000 --all

    # Run all checks against production
    uv run python scripts/smoke_test.py --base-url https://inbox.your-domain.com --all

    # Run a single step
    uv run python scripts/smoke_test.py --base-url ... --step health-check

Exit codes:
    0 — all checks passed
    1 — at least one check failed
    2 — invalid arguments / config

Each step is independent; ordering matters only for reporting clarity.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from dataclasses import dataclass, field
from typing import Awaitable, Callable

import httpx

# --- Output formatting ---

GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
DIM = "\033[2m"
RESET = "\033[0m"


@dataclass
class StepResult:
    name: str
    ok: bool
    duration_ms: int
    detail: str = ""
    warnings: list[str] = field(default_factory=list)


@dataclass
class Context:
    base_url: str
    internal_api_token: str | None
    timeout: float = 10.0


# --- Individual checks ---


async def check_health(ctx: Context) -> StepResult:
    """GET /health → 200 {status: ok}."""
    start = asyncio.get_event_loop().time()
    async with httpx.AsyncClient(timeout=ctx.timeout) as client:
        response = await client.get(f"{ctx.base_url}/health")
    duration_ms = int((asyncio.get_event_loop().time() - start) * 1000)

    if response.status_code != 200:
        return StepResult(
            name="health-check", ok=False, duration_ms=duration_ms,
            detail=f"expected 200, got {response.status_code}",
        )
    try:
        body = response.json()
        if body.get("status") != "ok":
            return StepResult(
                name="health-check", ok=False, duration_ms=duration_ms,
                detail=f"unexpected body: {body}",
            )
    except Exception as exc:
        return StepResult(
            name="health-check", ok=False, duration_ms=duration_ms,
            detail=f"parse error: {exc}",
        )

    return StepResult(name="health-check", ok=True, duration_ms=duration_ms)


async def check_ready_quick(ctx: Context) -> StepResult:
    """GET /ready/quick → 200 with postgres + redis up."""
    start = asyncio.get_event_loop().time()
    async with httpx.AsyncClient(timeout=ctx.timeout) as client:
        response = await client.get(f"{ctx.base_url}/ready/quick")
    duration_ms = int((asyncio.get_event_loop().time() - start) * 1000)

    if response.status_code != 200:
        return StepResult(
            name="ready-quick", ok=False, duration_ms=duration_ms,
            detail=f"status {response.status_code}, body: {response.text[:200]}",
        )
    body = response.json()
    if body.get("postgres") != "up" or body.get("redis") != "up":
        return StepResult(
            name="ready-quick", ok=False, duration_ms=duration_ms,
            detail=f"deps: {body}",
        )
    return StepResult(name="ready-quick", ok=True, duration_ms=duration_ms)


async def check_ready_full(ctx: Context) -> StepResult:
    """GET /ready → 200 with postgres + redis + worker up."""
    start = asyncio.get_event_loop().time()
    async with httpx.AsyncClient(timeout=ctx.timeout) as client:
        response = await client.get(f"{ctx.base_url}/ready")
    duration_ms = int((asyncio.get_event_loop().time() - start) * 1000)

    warnings: list[str] = []
    if response.status_code != 200:
        # 503 with worker:down is informative but maybe just startup delay
        try:
            body = response.json()
        except Exception:
            body = {}
        if body.get("worker", {}).get("status") == "down":
            warnings.append(
                "worker heartbeat not fresh — wait 2-3 min and re-check; "
                "if persists, worker may be crashed"
            )
        return StepResult(
            name="ready-full", ok=False, duration_ms=duration_ms,
            detail=f"status {response.status_code}, body: {body}",
            warnings=warnings,
        )
    return StepResult(name="ready-full", ok=True, duration_ms=duration_ms)


async def check_webhook_verification(ctx: Context) -> StepResult:
    """GET /webhooks/sendpulse?hub.challenge=X → echoes X."""
    start = asyncio.get_event_loop().time()
    async with httpx.AsyncClient(timeout=ctx.timeout) as client:
        response = await client.get(
            f"{ctx.base_url}/webhooks/sendpulse?hub.challenge=smoke_test_123",
        )
    duration_ms = int((asyncio.get_event_loop().time() - start) * 1000)

    if response.status_code != 200:
        return StepResult(
            name="webhook-verify", ok=False, duration_ms=duration_ms,
            detail=f"status {response.status_code}",
        )
    body = response.json()
    if body.get("hub.challenge") != "smoke_test_123":
        return StepResult(
            name="webhook-verify", ok=False, duration_ms=duration_ms,
            detail=f"challenge not echoed: {body}",
        )
    return StepResult(name="webhook-verify", ok=True, duration_ms=duration_ms)


async def check_webhook_post_accepts(ctx: Context) -> StepResult:
    """POST /webhooks/sendpulse with empty JSON → 200 (always-200 contract)."""
    start = asyncio.get_event_loop().time()
    async with httpx.AsyncClient(timeout=ctx.timeout) as client:
        response = await client.post(
            f"{ctx.base_url}/webhooks/sendpulse",
            json={"smoke_test": True},
        )
    duration_ms = int((asyncio.get_event_loop().time() - start) * 1000)

    if response.status_code != 200:
        return StepResult(
            name="webhook-post", ok=False, duration_ms=duration_ms,
            detail=(
                f"status {response.status_code} — webhook MUST always return 200 "
                f"per provider contract"
            ),
        )
    return StepResult(name="webhook-post", ok=True, duration_ms=duration_ms)


async def check_lead_api_auth(ctx: Context) -> StepResult:
    """GET /api/lead/X without token → 401; with wrong token → 401."""
    start = asyncio.get_event_loop().time()
    async with httpx.AsyncClient(timeout=ctx.timeout) as client:
        # No token
        r1 = await client.get(f"{ctx.base_url}/api/lead/smoketest")
        # Wrong token
        r2 = await client.get(
            f"{ctx.base_url}/api/lead/smoketest",
            headers={"X-Internal-Token": "definitely-wrong-token"},
        )
    duration_ms = int((asyncio.get_event_loop().time() - start) * 1000)

    if r1.status_code != 401:
        return StepResult(
            name="lead-api-auth", ok=False, duration_ms=duration_ms,
            detail=f"no-token expected 401, got {r1.status_code}",
        )
    if r2.status_code != 401:
        return StepResult(
            name="lead-api-auth", ok=False, duration_ms=duration_ms,
            detail=f"wrong-token expected 401, got {r2.status_code}",
        )
    return StepResult(name="lead-api-auth", ok=True, duration_ms=duration_ms)


async def check_lead_api_404(ctx: Context) -> StepResult:
    """GET /api/lead/nonexistent with valid token → 404."""
    if not ctx.internal_api_token:
        return StepResult(
            name="lead-api-404", ok=False, duration_ms=0,
            detail="INTERNAL_API_TOKEN not set in env — cannot test",
        )
    start = asyncio.get_event_loop().time()
    async with httpx.AsyncClient(timeout=ctx.timeout) as client:
        response = await client.get(
            f"{ctx.base_url}/api/lead/nonexistent_smoke",
            headers={"X-Internal-Token": ctx.internal_api_token},
        )
    duration_ms = int((asyncio.get_event_loop().time() - start) * 1000)

    if response.status_code != 404:
        return StepResult(
            name="lead-api-404", ok=False, duration_ms=duration_ms,
            detail=f"expected 404, got {response.status_code}",
        )
    return StepResult(name="lead-api-404", ok=True, duration_ms=duration_ms)


async def check_admin_reachable(ctx: Context, admin_url: str | None) -> StepResult:
    """GET admin URL → 200 (login page) or 401."""
    if not admin_url:
        return StepResult(
            name="admin-reachable", ok=True, duration_ms=0,
            detail="skipped (no --admin-url provided)",
        )
    start = asyncio.get_event_loop().time()
    async with httpx.AsyncClient(timeout=ctx.timeout, follow_redirects=True) as client:
        response = await client.get(admin_url)
    duration_ms = int((asyncio.get_event_loop().time() - start) * 1000)

    if response.status_code >= 500:
        return StepResult(
            name="admin-reachable", ok=False, duration_ms=duration_ms,
            detail=f"server error {response.status_code}",
        )
    # 200 (login form) or 401 (basic auth challenge) are both fine
    return StepResult(
        name="admin-reachable", ok=True, duration_ms=duration_ms,
        detail=f"status {response.status_code}",
    )


async def check_https_redirect(ctx: Context) -> StepResult:
    """If base_url is https://, verify http:// redirects to it."""
    if not ctx.base_url.startswith("https://"):
        return StepResult(
            name="https-redirect", ok=True, duration_ms=0,
            detail="skipped (base_url is not https)",
        )
    http_url = ctx.base_url.replace("https://", "http://", 1) + "/health"
    start = asyncio.get_event_loop().time()
    async with httpx.AsyncClient(timeout=ctx.timeout, follow_redirects=False) as client:
        response = await client.get(http_url)
    duration_ms = int((asyncio.get_event_loop().time() - start) * 1000)

    if response.status_code not in (301, 302, 307, 308):
        return StepResult(
            name="https-redirect", ok=False, duration_ms=duration_ms,
            detail=f"expected redirect (3xx), got {response.status_code}",
        )
    location = response.headers.get("location", "")
    if not location.startswith("https://"):
        return StepResult(
            name="https-redirect", ok=False, duration_ms=duration_ms,
            detail=f"location not https: {location}",
        )
    return StepResult(name="https-redirect", ok=True, duration_ms=duration_ms)


# --- Step registry ---

STEPS: dict[str, Callable[[Context], Awaitable[StepResult]]] = {
    "health-check": check_health,
    "ready-quick": check_ready_quick,
    "ready-full": check_ready_full,
    "webhook-verify": check_webhook_verification,
    "webhook-post": check_webhook_post_accepts,
    "lead-api-auth": check_lead_api_auth,
    "lead-api-404": check_lead_api_404,
    "https-redirect": check_https_redirect,
}


def print_result(r: StepResult) -> None:
    status_icon = f"{GREEN}✓{RESET}" if r.ok else f"{RED}✗{RESET}"
    duration_str = f"{DIM}({r.duration_ms}ms){RESET}"
    line = f"  {status_icon} {r.name:24} {duration_str}"
    if r.detail:
        line += f" {DIM}— {r.detail}{RESET}"
    print(line)
    for w in r.warnings:
        print(f"     {YELLOW}⚠ {w}{RESET}")


async def run_all(ctx: Context, admin_url: str | None) -> list[StepResult]:
    results = []
    for name, fn in STEPS.items():
        result = await fn(ctx)
        results.append(result)
        print_result(result)
    # Admin reachability — separate since it has a different URL
    admin_result = await check_admin_reachable(ctx, admin_url)
    results.append(admin_result)
    print_result(admin_result)
    return results


async def run_one(ctx: Context, step_name: str) -> StepResult:
    if step_name not in STEPS:
        print(f"{RED}Unknown step: {step_name}. Available: {list(STEPS.keys())}{RESET}")
        sys.exit(2)
    result = await STEPS[step_name](ctx)
    print_result(result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="social_inbox smoke tests")
    parser.add_argument("--base-url", required=True, help="API base URL (e.g. https://inbox.example.com)")
    parser.add_argument("--admin-url", help="Admin dashboard URL (optional, e.g. https://inbox-admin.example.com)")
    parser.add_argument("--step", help="Run a single step (default: run all)")
    parser.add_argument("--all", action="store_true", help="Run all steps")
    parser.add_argument("--timeout", type=float, default=10.0, help="Per-request timeout in seconds")
    args = parser.parse_args()

    if not args.step and not args.all:
        print(f"{RED}Specify --all or --step <name>{RESET}")
        sys.exit(2)

    internal_token = os.environ.get("INTERNAL_API_TOKEN")

    ctx = Context(
        base_url=args.base_url.rstrip("/"),
        internal_api_token=internal_token,
        timeout=args.timeout,
    )

    print(f"\nSmoke checks against {ctx.base_url}")
    print(f"INTERNAL_API_TOKEN: {'set' if internal_token else 'not set (some checks will skip)'}")
    print()

    if args.all:
        results = asyncio.run(run_all(ctx, args.admin_url))
        passed = sum(1 for r in results if r.ok)
        total = len(results)
        print()
        if passed == total:
            print(f"{GREEN}All {total} checks passed.{RESET}")
            sys.exit(0)
        else:
            print(f"{RED}{total - passed} of {total} checks FAILED.{RESET}")
            sys.exit(1)
    else:
        result = asyncio.run(run_one(ctx, args.step))
        sys.exit(0 if result.ok else 1)


if __name__ == "__main__":
    main()
```

### 2. Makefile target

a) В `Makefile` добавить:

```makefile
smoke-local:
	uv run python scripts/smoke_test.py --base-url http://localhost:8000 --all

smoke-prod:
	@if [ -z "$$PROD_BASE_URL" ]; then \
		echo "Set PROD_BASE_URL, e.g.: PROD_BASE_URL=https://inbox.your-domain.com make smoke-prod"; \
		exit 1; \
	fi
	uv run python scripts/smoke_test.py --base-url $$PROD_BASE_URL --admin-url $$PROD_ADMIN_URL --all
```

### 3. Технический runbook для Виктора

a) Создать `docs/go_live_runbook.md`:

```markdown
# Go-Live Runbook (для Виктора)

Технический deep-dive по запуску. Юлин чек-лист — в `Go_Live_Checklist.docx`.

## За 3 дня до запуска (T-3)

### Pre-flight checks

```bash
ssh deploy@vps
cd /opt/social_inbox
./deploy/scripts/env_check.sh                # все ключи на месте
docker compose ps                            # все 6 сервисов running
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

Все шаги должны быть `✓`.

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

### Codе rollback

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
docker compose -f docker-compose.yml -f deploy/docker-compose.prod.yml stop
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
```

### 4. Юлин Go-Live Checklist (DOCX)

a) Создать `scripts/build_go_live_checklist.js`:

```javascript
const { Document, Packer, Paragraph, TextRun, Table, TableRow, TableCell,
        Header, Footer, AlignmentType, LevelFormat, HeadingLevel,
        BorderStyle, WidthType, ShadingType, PageNumber, PageBreak } = require('docx');
const fs = require('fs');

const FONT = "Arial";
const ORANGE = "E86854";
const GREEN = "2E7D32";
const RED = "C62828";
const GRAY = "888888";
const LIGHT_BG = "FFF4E6";
const SUCCESS_BG = "E8F5E9";
const DANGER_BG = "FFEBEE";

const border = { style: BorderStyle.SINGLE, size: 1, color: "CCCCCC" };
const borders = { top: border, bottom: border, left: border, right: border };
const cellMargins = { top: 80, bottom: 80, left: 120, right: 120 };

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

const Bullet = (parts, level = 0) => new Paragraph({
  numbering: { reference: "bullets", level },
  spacing: { after: 60 },
  children: parts.map(r => new TextRun({ ...r, font: FONT })),
});

const Check = (text) => new Paragraph({
  spacing: { after: 80 },
  children: [
    new TextRun({ text: "☐  ", bold: true, size: 22, font: FONT }),
    new TextRun({ text, size: 22, font: FONT }),
  ],
});

const Note = (parts, color = ORANGE, bg = LIGHT_BG) => new Paragraph({
  spacing: { before: 100, after: 100 },
  shading: { fill: bg, type: ShadingType.CLEAR },
  border: { left: { style: BorderStyle.SINGLE, size: 12, color, space: 8 } },
  indent: { left: 200 },
  children: parts.map(r => new TextRun({ ...r, font: FONT })),
});

const Warning = (parts) => Note(
  [{ text: "⚠️ ", bold: true }, ...parts],
  RED, DANGER_BG,
);

const Success = (parts) => Note(
  [{ text: "✓ ", bold: true, color: GREEN }, ...parts],
  GREEN, SUCCESS_BG,
);

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
    children: [new TextRun({ text: "Запуск social_inbox", bold: true, size: 56, font: FONT, color: ORANGE })],
  }),
  new Paragraph({
    alignment: AlignmentType.CENTER,
    spacing: { after: 400 },
    children: [new TextRun({ text: "Чек-лист и инструкция для Юлии", size: 28, font: FONT })],
  }),
  new Paragraph({
    alignment: AlignmentType.CENTER,
    spacing: { after: 200 },
    children: [new TextRun({ text: "Как мы будем запускать автоматизацию Instagram-воронки", italics: true, size: 22, font: FONT, color: "555555" })],
  }),
  new Paragraph({ children: [new PageBreak()] }),

  // What we're launching
  H1("Что мы запускаем"),
  P("После нескольких недель разработки у нас готова система автоматизации Instagram-воронки. Что она умеет:"),
  Bullet([{ text: "Подписчик пишет «ОЧИЩЕНИЕ» в комментариях под твоим Reels → автоматически получает DM с приглашением в Telegram" }]),
  Bullet([{ text: "Подписчик пишет тебе в DM первый раз → получает приветственное сообщение с переходом в Telegram" }]),
  Bullet([{ text: "Подписчик задаёт вопросы в DM → AI (Claude) отвечает дружелюбно и кратко" }]),
  Bullet([{ text: "Если вопрос про симптомы / болезнь / беременность → AI сразу передаёт тебе" }]),
  Bullet([{ text: "Если подписчик пишет «оператор» → передаёт тебе" }]),
  Bullet([{ text: "Ты видишь все handover-диалоги в админке и отвечаешь лично" }]),
  Bullet([{ text: "Каждое утро получаешь сводку вчерашних результатов" }]),

  Note([
    { text: "Главный показатель успеха: ", bold: true },
    { text: "сколько подписчиков из Instagram реально дошли до твоего Telegram-бота и прошли квиз. Эту метрику видно в админке." },
  ]),

  HR(),

  // Запуск пошагово
  H1("План запуска"),
  P("Запускаем не сразу всё, а постепенно (canary rollout). Это снижает риски: если что-то идёт не так, мы видим это на одном Reels, а не на всём контенте сразу."),

  H2("Подготовка (за 2-3 дня до запуска)"),
  Check("Все credentials и доступы переданы Виктору (SendPulse, домен, VPS)"),
  Check("Виктор подтвердил, что smoke tests прошли"),
  Check("Notification bot работает — ты получила тестовое сообщение"),
  Check("Daily digest пришёл утром — ты видишь сводку"),
  Check("Ты потренировалась заходить в админку (https://inbox-admin.<domain>)"),
  Check("Ты потренировалась отвечать на handover в админке"),
  Check("Бэкапы работают — Виктор показал, что pg_dump запускается раз в день"),

  H2("День запуска"),
  P("Это сам день, когда первый Reels пойдёт с автоматизацией."),

  Check("Утро: проверь, что админка открывается"),
  Check("Утро: попроси Виктора запустить финальную smoke-проверку"),
  Check("В админке проверь: keyword «очищение» активен (Ключевые слова → найди в списке)"),
  Check("В админке проверь: scenario «default_purify_comment» активен (Сценарии → найди в списке)"),
  Check("Опубликуй ОДИН Reels с CTA «Напиши ОЧИЩЕНИЕ в комментариях»"),

  Warning([
    { text: "Только один Reels на день запуска. ", bold: true },
    { text: "Не публикуй сразу 5 видео с разными ключевыми словами. Сначала наблюдаем 24 часа, потом расширяем." },
  ]),

  Check("Попроси кого-то (Виктора, подругу с другого аккаунта) написать «ОЧИЩЕНИЕ» в комментариях под Reels"),
  Check("Подожди 1-2 минуты"),
  Check("Этот человек должен получить DM с приглашением в Telegram"),
  Check("Кликни на «Перейти в Telegram» в этом DM — должно открыться @yuliya_purify_bot"),
  Check("В bot_purify должно быть персонализированное приветствие («Привет, ...! Здорово, что заинтересовалась программой Очищение...»)"),

  Success([
    { text: "Если все шаги прошли успешно — система работает. ", bold: true },
    { text: "Можно идти дальше." },
  ]),

  HR(),

  H2("Первые 24 часа"),
  P("Главная задача — наблюдать. Не паниковать, не делать резких движений."),

  Bullet([{ text: "Раз в 2-3 часа открывай админку → раздел «Входящие»" }]),
  Bullet([{ text: "Если есть handover-диалоги (🔴) — отвечай в админке через форму «Ответ от Юли»" }]),
  Bullet([{ text: "Получаешь в Telegram уведомление о handover? Зайди в админку и ответь оттуда" }]),
  Bullet([{ text: "Если что-то странное — скрин и Виктору" }]),

  P("Что в принципе НЕ должно происходить (если случается — Виктору):"),
  Bullet([{ text: "Подписчики получают DM с медицинскими утверждениями («вылечит», «гарантирую»)" }, ]),
  Bullet([{ text: "Бот отвечает по 5 раз на одно сообщение" }]),
  Bullet([{ text: "DM приходит спустя 10+ минут (норма — 30 секунд)" }]),
  Bullet([{ text: "Юля не получает уведомления о handover" }]),

  HR(),

  H2("День 2-7: расширение"),
  P("Если за 24 часа всё работало стабильно, плавно расширяем:"),

  Check("День 2: добавь keyword под второй Reels (например, «МАСЛА» если делаешь о маслах)"),
  Check("День 3-4: следи за статистикой в админке — сколько лидов, сколько conversion"),
  Check("День 5-7: если всё хорошо, можно использовать на постоянной основе"),

  Note([
    { text: "Как добавить новый keyword: ", bold: true },
    { text: "Админка → Ключевые слова → Добавить keyword → введи слово, тип «contains», где «comment», сценарий «default_purify_comment» или другой." },
  ]),

  HR(),

  // ROLLBACK
  H1("Если что-то пошло не так"),

  H2("Уровень 1: Странный диалог"),
  P("Сценарий: один подписчик получает странный ответ от AI."),
  Bullet([{ text: "Открой этот диалог в админке" }]),
  Bullet([{ text: "Переключатель «AI режим включён» → выключи" }]),
  Bullet([{ text: "Этот человек больше не получит ответов от AI; ты можешь ответить лично через форму" }]),

  H2("Уровень 2: Жалоба или спам"),
  P("Сценарий: один человек жалуется на бота / много спам-комментариев / странные DM."),
  Bullet([{ text: "Зайди в админку → Ключевые слова" }]),
  Bullet([{ text: "Деактивируй (выключи галочку «Активен») у проблемного keyword" }]),
  Bullet([{ text: "Acquisition остановлен, существующие диалоги продолжаются как раньше" }]),
  Bullet([{ text: "Напиши Виктору — разберёмся в причине" }]),

  H2("Уровень 3: Что-то совсем не работает"),
  P("Сценарий: бот вообще не отвечает / ошибки в админке / Виктор недоступен."),
  Bullet([{ text: "Зайди в SendPulse → Чат-боты" }]),
  Bullet([{ text: "Отключи webhook (одна кнопка)" }]),
  Bullet([{ text: "Подписчики, написавшие в DM, не получат ответа — это нормально для emergency" }]),
  Bullet([{ text: "Telegram-бот @yuliya_purify_bot продолжает работать отдельно — это другой сервис" }]),

  Warning([
    { text: "В emergency-ситуации лучше отключиться, чем продолжать работу с проблемами. ", bold: true },
    { text: "Подписчик, не получивший ответа, гораздо лучше чем подписчик, получивший неправильный или потенциально опасный ответ." },
  ]),

  HR(),

  // CONTACTS
  H1("Контакты для emergency"),

  P([{ text: "Виктор: ", bold: true }, { text: "Telegram, прямой звонок, WhatsApp" }]),
  P([{ text: "SendPulse поддержка: ", bold: true }, { text: "support@sendpulse.com" }]),
  P([{ text: "Anthropic поддержка (если AI вообще не отвечает): ", bold: true }, { text: "support@anthropic.com" }]),

  HR(),

  // FINAL
  H1("После запуска"),
  P("Через неделю стабильной работы возвращаемся к обычной жизни:"),

  Bullet([{ text: "Раз в день открой админку утром, проверь handover-диалоги" }]),
  Bullet([{ text: "Раз в день читай daily digest в Telegram" }]),
  Bullet([{ text: "Под новые Reels добавляй keyword за 30 секунд через админку" }]),
  Bullet([{ text: "Раз в неделю смотри статистику — растёт ли conversion" }]),

  Success([
    { text: "Молодец, что дошла досюда. ", bold: true },
    { text: "Эта система должна экономить тебе несколько часов в неделю и масштабировать аудиторию без проседания качества общения. Если ощущаешь, что что-то можно улучшить — скажи, мы доработаем 💚" },
  ]),
];

const doc = new Document({
  creator: "Claude",
  title: "Go-Live Checklist",
  styles: {
    default: { document: { run: { font: FONT, size: 22 } } },
    paragraphStyles: [
      { id: "Heading1", name: "Heading 1", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 32, bold: true, font: FONT, color: ORANGE },
        paragraph: { spacing: { before: 360, after: 200 }, outlineLevel: 0 } },
      { id: "Heading2", name: "Heading 2", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 26, bold: true, font: FONT },
        paragraph: { spacing: { before: 280, after: 140 }, outlineLevel: 1 } },
    ],
  },
  numbering: {
    config: [
      { reference: "bullets",
        levels: [
          { level: 0, format: LevelFormat.BULLET, text: "•", alignment: AlignmentType.LEFT,
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
          children: [new TextRun({ text: "social_inbox · go-live", italics: true, color: GRAY, size: 18, font: FONT })],
        })],
      }),
    },
    footers: {
      default: new Footer({
        children: [new Paragraph({
          alignment: AlignmentType.CENTER,
          children: [
            new TextRun({ children: ["Стр. ", PageNumber.CURRENT, " из ", PageNumber.TOTAL_PAGES], size: 18, font: FONT, color: GRAY }),
          ],
        })],
      }),
    },
    children,
  }],
});

Packer.toBuffer(doc).then(buf => {
  const out = process.argv[2] || "/home/claude/task18/Go_Live_Checklist.docx";
  fs.writeFileSync(out, buf);
  console.log("OK:", out, "size:", buf.length);
});
```

b) Запустить скрипт в Node.js (на машине Виктора):

```bash
cd D:\Work\social_inbox
node scripts/build_go_live_checklist.js docs/Go_Live_Checklist.docx
```

   Это создаст финальный DOCX в `docs/`. Файл можно отдать Юле.

   **Замечание:** скрипт использует библиотеку `docx` (Node.js). Если у Виктора не установлен Node.js — пропускает шаг 4b, я могу прислать собранный DOCX отдельно.

### 5. CLAUDE.md обновление

a) В `CLAUDE.md` § 17 (roadmap) пометить Task 18 как реализованный.

b) В CLAUDE.md в самый верх (после заголовка проекта) добавить ссылку на runbook'и:

```markdown
## Запуск в production

См. `docs/go_live_runbook.md` для технической процедуры запуска.
См. `docs/Go_Live_Checklist.docx` для пошаговой инструкции Юле.
Smoke checks: `make smoke-prod` (требует PROD_BASE_URL в окружении).
```

---

## Acceptance criteria

- [ ] Файлы созданы по структуре подзадач 1–5
- [ ] `make lint` проходит на `scripts/smoke_test.py`
- [ ] `python scripts/smoke_test.py --base-url http://localhost:8000 --all` локально (с запущенным compose) проходит все шаги
- [ ] `make smoke-local` работает как алиас
- [ ] `docs/go_live_runbook.md` написан, читабелен
- [ ] `docs/Go_Live_Checklist.docx` сгенерирован (или предоставлен Виктором), читается в Word
- [ ] CLAUDE.md обновлён в §17 и в шапке
- [ ] **Главное:** после деплоя в production (Task 17) запустить `PROD_BASE_URL=https://inbox.your-domain.com make smoke-prod` — все шаги проходят
- [ ] Юля прочитала Go_Live_Checklist.docx и задала уточняющие вопросы

---

## Do NOT

- НЕ запускать smoke tests с продакшен `INTERNAL_API_TOKEN` с публичных машин (он засветится в process list). Запускай с VPS или с локальной dev машины из защищённого окружения.
- НЕ делать canary rollout сразу на 5 Reels. Один Reels день 0, расширение со дня 2.
- НЕ удалять старый keyword «очищение» если решишь добавить новый. Можно несколько keywords указывать на один scenario, или деактивировать через флаг `active=FALSE` вместо удаления (история сохранится).
- НЕ запускать `make smoke-prod` каждые 5 минут как мониторинг — для этого есть `/ready` endpoint и uptime-monitoring сервисы (UptimeRobot, Better Stack — бесплатные на нашем масштабе).
- НЕ давать `INTERNAL_API_TOKEN` Юле «на всякий случай». Это техническая переменная — только у Виктора в `.env`.
- НЕ менять `system_smart.md` (system prompt Claude) посреди запуска. Если хочется улучшить — отдельный итеративный цикл после стабилизации.
- НЕ обещать Юле, что conversion будет такой-то процент. Реальные цифры будут видны после 2-4 недель работы. Сейчас задача — стабильно запустить.

---

## Зависимости задачи

- Все предыдущие Tasks применены: 01, 03, 04, 05, 06, 07, 08, 09, 11, 13, 14, 15, 16, 17
- Production развёрнут на VPS (Task 17 выполнен)
- Юля доступна для чтения чек-листа и для проведения тестового Reels в день T-0
- Виктор доступен в день запуска как первая линия поддержки

---

## Что после этой задачи

После применения Task 18:

```
✅ Smoke test script запускается одной командой
✅ Юлин Go-Live Checklist на руках
✅ Виктор знает точную процедуру первого дня по runbook'у
✅ Rollback procedures документированы
✅ Emergency contacts определены
```

**После прохождения чек-листа дня T-0 — проект ЗАПУЩЕН.**

Дальше — операционная фаза. Roadmap из CLAUDE.md выполнен полностью. Будущие улучшения:

- A/B testing разных welcome-сообщений
- Vision-mode Claude (если SendPulse начнёт присылать изображения из DM)
- Расширение на TikTok когда они откроют DM API
- Многоязычность (Polish, Lithuanian) когда Юля начнёт работать с локальной аудиторией
- Перенос с SendPulse на собственное Meta App (требует юрлицо)

Эти задачи — за рамками текущего roadmap. Включаем в backlog когда первые результаты будут видны.

---

**Дата создания:** 2026-05-08
**Применять в:** `D:\Work\social_inbox` после Task 17
**Эстимейт:** 3–4 часа на Claude Code (генерация скриптов и DOCX) + 1 день Виктора на T-3..T-1 + 1 день Юлия+Виктор на T-0
