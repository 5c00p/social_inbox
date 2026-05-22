"""SendPulse REST API client.

Handles:
- OAuth2 client_credentials flow with token caching in Redis
- HTTP retries on transient errors (5xx, 429)
- Graceful 401 handling (refresh token and retry once)
- No retries on 403 (Free tier limitation — wastes API quota)

Does NOT contain provider-level logic (event parsing, cursor management).
That stays in app/providers/sendpulse.py.

Docs: https://sendpulse.com/integrations/api/chatbot/instagram
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from app.repos.redis_client import get_redis
from app.utils.logging import get_logger

log = get_logger(__name__)

TOKEN_REDIS_KEY = "sendpulse:access_token"
TOKEN_TTL_SECONDS = 50 * 60  # SendPulse issues 60-min tokens; refresh 10 min early


class SendPulseAuthError(Exception):
    """OAuth or token refresh failed."""


class SendPulseAPIError(Exception):
    """Non-2xx response after retries."""

    def __init__(self, status: int, body: str) -> None:
        self.status = status
        self.body = body
        super().__init__(f"SendPulse API error {status}: {body[:200]}")


class SendPulseClient:
    """Async HTTP client for SendPulse Instagram API."""

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        base_url: str = "https://api.sendpulse.com",
        timeout: float = 15.0,
    ) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._base_url = base_url.rstrip("/")
        self._timeout = httpx.Timeout(timeout)

    # ---- Auth ----

    async def _fetch_new_token(self) -> str:
        """Request a new OAuth access token from SendPulse."""
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(
                f"{self._base_url}/oauth/access_token",
                json={
                    "grant_type": "client_credentials",
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                },
            )
        if response.status_code != 200:
            raise SendPulseAuthError(
                f"OAuth failed: status={response.status_code}, body={response.text[:300]}"
            )
        data = response.json()
        token = data.get("access_token")
        if not token:
            raise SendPulseAuthError(f"OAuth response missing access_token: {data}")
        return str(token)

    async def _get_token(self, force_refresh: bool = False) -> str:
        """Return cached token or fetch a new one."""
        redis = await get_redis()
        if not force_refresh:
            cached = await redis.get(TOKEN_REDIS_KEY)
            if cached:
                return str(cached)

        token = await self._fetch_new_token()
        await redis.set(TOKEN_REDIS_KEY, token, ex=TOKEN_TTL_SECONDS)
        log.info("sendpulse_token_refreshed")
        return token

    # ---- HTTP wrapper ----

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        max_retries: int = 3,
    ) -> dict[str, Any]:
        """Perform an authenticated API request with retries on transient errors.

        Returns parsed JSON body on 2xx. Raises SendPulseAPIError otherwise.
        On 401: refreshes token once and retries.
        On 5xx / 429: exponential backoff up to max_retries.
        On 403: raises immediately (likely Free tier limitation — don't retry).
        """
        url = f"{self._base_url}{path}"
        token_refreshed = False

        for attempt in range(max_retries + 1):
            token = await self._get_token()
            headers = {"Authorization": f"Bearer {token}"}

            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.request(
                    method,
                    url,
                    params=params,
                    json=json,
                    headers=headers,
                )

            if 200 <= response.status_code < 300:
                if not response.content:
                    return {}
                parsed = response.json()
                if isinstance(parsed, dict):
                    return parsed
                # Some endpoints return arrays at the top level; wrap so callers
                # can rely on dict access.
                return {"data": parsed}

            if response.status_code == 401 and not token_refreshed:
                log.warning("sendpulse_401_refreshing_token")
                await self._get_token(force_refresh=True)
                token_refreshed = True
                continue

            if response.status_code == 403:
                raise SendPulseAPIError(403, response.text)

            if response.status_code in (429, 500, 502, 503, 504) and attempt < max_retries:
                backoff = 2**attempt
                log.warning(
                    "sendpulse_transient_error_retrying",
                    status=response.status_code,
                    attempt=attempt + 1,
                    backoff=backoff,
                )
                await asyncio.sleep(backoff)
                continue

            raise SendPulseAPIError(response.status_code, response.text)

        raise SendPulseAPIError(0, f"exhausted {max_retries} retries")
