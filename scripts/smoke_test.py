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
import os
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

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
            name="health-check",
            ok=False,
            duration_ms=duration_ms,
            detail=f"expected 200, got {response.status_code}",
        )
    try:
        body = response.json()
        if body.get("status") != "ok":
            return StepResult(
                name="health-check",
                ok=False,
                duration_ms=duration_ms,
                detail=f"unexpected body: {body}",
            )
    except Exception as exc:
        return StepResult(
            name="health-check",
            ok=False,
            duration_ms=duration_ms,
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
            name="ready-quick",
            ok=False,
            duration_ms=duration_ms,
            detail=f"status {response.status_code}, body: {response.text[:200]}",
        )
    body = response.json()
    if body.get("postgres") != "up" or body.get("redis") != "up":
        return StepResult(
            name="ready-quick",
            ok=False,
            duration_ms=duration_ms,
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
            name="ready-full",
            ok=False,
            duration_ms=duration_ms,
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
            name="webhook-verify",
            ok=False,
            duration_ms=duration_ms,
            detail=f"status {response.status_code}",
        )
    body = response.json()
    if body.get("hub.challenge") != "smoke_test_123":
        return StepResult(
            name="webhook-verify",
            ok=False,
            duration_ms=duration_ms,
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
            name="webhook-post",
            ok=False,
            duration_ms=duration_ms,
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
        r1 = await client.get(f"{ctx.base_url}/api/lead/smoketest")
        r2 = await client.get(
            f"{ctx.base_url}/api/lead/smoketest",
            headers={"X-Internal-Token": "definitely-wrong-token"},
        )
    duration_ms = int((asyncio.get_event_loop().time() - start) * 1000)

    if r1.status_code != 401:
        return StepResult(
            name="lead-api-auth",
            ok=False,
            duration_ms=duration_ms,
            detail=f"no-token expected 401, got {r1.status_code}",
        )
    if r2.status_code != 401:
        return StepResult(
            name="lead-api-auth",
            ok=False,
            duration_ms=duration_ms,
            detail=f"wrong-token expected 401, got {r2.status_code}",
        )
    return StepResult(name="lead-api-auth", ok=True, duration_ms=duration_ms)


async def check_lead_api_404(ctx: Context) -> StepResult:
    """GET /api/lead/nonexistent with valid token → 404."""
    if not ctx.internal_api_token:
        return StepResult(
            name="lead-api-404",
            ok=False,
            duration_ms=0,
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
            name="lead-api-404",
            ok=False,
            duration_ms=duration_ms,
            detail=f"expected 404, got {response.status_code}",
        )
    return StepResult(name="lead-api-404", ok=True, duration_ms=duration_ms)


async def check_admin_reachable(ctx: Context, admin_url: str | None) -> StepResult:
    """GET admin URL → 200 (login page) or 401."""
    if not admin_url:
        return StepResult(
            name="admin-reachable",
            ok=True,
            duration_ms=0,
            detail="skipped (no --admin-url provided)",
        )
    start = asyncio.get_event_loop().time()
    async with httpx.AsyncClient(timeout=ctx.timeout, follow_redirects=True) as client:
        response = await client.get(admin_url)
    duration_ms = int((asyncio.get_event_loop().time() - start) * 1000)

    if response.status_code >= 500:
        return StepResult(
            name="admin-reachable",
            ok=False,
            duration_ms=duration_ms,
            detail=f"server error {response.status_code}",
        )
    return StepResult(
        name="admin-reachable",
        ok=True,
        duration_ms=duration_ms,
        detail=f"status {response.status_code}",
    )


async def check_https_redirect(ctx: Context) -> StepResult:
    """If base_url is https://, verify http:// redirects to it."""
    if not ctx.base_url.startswith("https://"):
        return StepResult(
            name="https-redirect",
            ok=True,
            duration_ms=0,
            detail="skipped (base_url is not https)",
        )
    http_url = ctx.base_url.replace("https://", "http://", 1) + "/health"
    start = asyncio.get_event_loop().time()
    async with httpx.AsyncClient(timeout=ctx.timeout, follow_redirects=False) as client:
        response = await client.get(http_url)
    duration_ms = int((asyncio.get_event_loop().time() - start) * 1000)

    if response.status_code not in (301, 302, 307, 308):
        return StepResult(
            name="https-redirect",
            ok=False,
            duration_ms=duration_ms,
            detail=f"expected redirect (3xx), got {response.status_code}",
        )
    location = response.headers.get("location", "")
    if not location.startswith("https://"):
        return StepResult(
            name="https-redirect",
            ok=False,
            duration_ms=duration_ms,
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
    status_icon = f"{GREEN}OK{RESET}" if r.ok else f"{RED}FAIL{RESET}"
    duration_str = f"{DIM}({r.duration_ms}ms){RESET}"
    line = f"  [{status_icon}] {r.name:24} {duration_str}"
    if r.detail:
        line += f" {DIM}- {r.detail}{RESET}"
    print(line)
    for w in r.warnings:
        print(f"     {YELLOW}! {w}{RESET}")


async def run_all(ctx: Context, admin_url: str | None) -> list[StepResult]:
    results = []
    for _name, fn in STEPS.items():
        result = await fn(ctx)
        results.append(result)
        print_result(result)
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
    parser.add_argument(
        "--base-url",
        required=True,
        help="API base URL (e.g. https://inbox.example.com)",
    )
    parser.add_argument(
        "--admin-url",
        help="Admin dashboard URL (optional, e.g. https://inbox-admin.example.com)",
    )
    parser.add_argument("--step", help="Run a single step (default: run all)")
    parser.add_argument("--all", action="store_true", help="Run all steps")
    parser.add_argument(
        "--timeout",
        type=float,
        default=10.0,
        help="Per-request timeout in seconds",
    )
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
