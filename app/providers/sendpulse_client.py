"""SendPulse REST API client.

Wraps the SendPulse Instagram Chatbot API. Endpoints match the official
OpenAPI spec at https://sendpulse.com/swagger/instagram/.

Responsibilities:
- OAuth2 client_credentials flow with token caching in Redis
- HTTP retries on transient errors (5xx, 429)
- Graceful 401 handling (refresh token and retry once)
- Typed methods for the endpoints we actually use: list_chats,
  list_chat_messages, send_message, get_contact

Does NOT contain provider-level logic (event parsing, cursor management).
That stays in app/providers/sendpulse.py.
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

    def __init__(self, status: int, body: str, *, path: str = "") -> None:
        self.status = status
        self.body = body
        self.path = path
        super().__init__(f"SendPulse API error {status} on {path}: {body[:200]}")


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
        redis = await get_redis()
        if not force_refresh:
            cached = await redis.get(TOKEN_REDIS_KEY)
            if cached:
                return str(cached)
        token = await self._fetch_new_token()
        await redis.set(TOKEN_REDIS_KEY, token, ex=TOKEN_TTL_SECONDS)
        log.info("sendpulse_token_refreshed")
        return token

    # ---- Generic request wrapper ----

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        max_retries: int = 3,
        absolute_url: str | None = None,
    ) -> dict[str, Any]:
        """Perform an authenticated API request with retries.

        Either `path` (relative) or `absolute_url` (full URL, used for pagination
        via links.next) must be provided.
        """
        url = absolute_url or f"{self._base_url}{path}"
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
                return {"data": parsed}

            if response.status_code == 401 and not token_refreshed:
                log.warning("sendpulse_401_refreshing_token", path=path)
                await self._get_token(force_refresh=True)
                token_refreshed = True
                continue

            if response.status_code == 403:
                raise SendPulseAPIError(403, response.text, path=path)

            if response.status_code in (429, 500, 502, 503, 504) and attempt < max_retries:
                backoff = 2**attempt
                log.warning(
                    "sendpulse_transient_error_retrying",
                    status=response.status_code,
                    path=path,
                    attempt=attempt + 1,
                    backoff=backoff,
                )
                await asyncio.sleep(backoff)
                continue

            raise SendPulseAPIError(response.status_code, response.text, path=path)

        raise SendPulseAPIError(0, f"exhausted {max_retries} retries", path=path)

    # ---- Typed endpoints ----

    async def list_chats(
        self,
        bot_id: str,
        *,
        size: int = 50,
        skip: int = 0,
    ) -> dict[str, Any]:
        """GET /instagram/chats — list chats with subscribers."""
        return await self.request(
            "GET",
            "/instagram/chats",
            params={"bot_id": bot_id, "size": size, "skip": skip},
        )

    async def list_chats_next(self, next_url: str) -> dict[str, Any]:
        """Continue pagination using a links.next URL from a previous response."""
        return await self.request("GET", "", absolute_url=next_url)

    async def list_chat_messages(
        self,
        contact_id: str,
        *,
        size: int = 20,
        order: str = "desc",
    ) -> dict[str, Any]:
        """GET /instagram/chats/messages — messages for a specific contact.

        Used for deep context fetch when Claude needs more history than what's
        in our DB. Not used in polling — polling reads only the last message
        from /chats.
        """
        return await self.request(
            "GET",
            "/instagram/chats/messages",
            params={"contact_id": contact_id, "size": size, "order": order},
        )

    async def send_message(
        self,
        contact_id: str,
        messages: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """POST /instagram/contacts/send — send DM to a contact."""
        return await self.request(
            "POST",
            "/instagram/contacts/send",
            json={"contact_id": contact_id, "messages": messages},
        )

    async def get_contact(self, contact_id: str) -> dict[str, Any]:
        """GET /instagram/contacts/get — get contact info by ID."""
        return await self.request(
            "GET",
            "/instagram/contacts/get",
            params={"id": contact_id},
        )
