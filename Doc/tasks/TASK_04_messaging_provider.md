# Task 04: MessagingProvider interface

> Применить в `D:\Work\social_inbox` после успешного завершения Task 03. Положить файл в корень проекта, открыть в VS Code, попросить Claude Code: «Прочитай TASK_04_messaging_provider.md и выполни все подзадачи последовательно. Перед каждой подзадачей кратко опиши план, после — что фактически сделано».

---

## Контекст

После Task 03 у нас есть рабочая БД и data layer. Теперь нужен **абстрактный интерфейс** для общения с messaging-платформами (SendPulse сейчас, Manychat и Meta — потенциально в будущем).

Эта задача создаёт фундамент:

- **`MessagingProvider` ABC** — абстрактный класс с тремя методами: `parse_webhook`, `send`, `fetch_user_profile`
- **`IncomingEvent`** — нормализованная Pydantic-модель входящего события (DM, comment, postback)
- **`OutgoingMessage`** — модель исходящего сообщения (text, quick_replies, media, private_reply_to_comment)
- **Provider factory** — `get_provider()` возвращает singleton нужного провайдера на основе конфигурации
- **Заглушки** для `SendPulseProvider` (полная реализация в Task 05), `ManychatProvider`, `MetaProvider`
- **`FakeProvider`** в `tests/fakes/` — для тестов webhook handler'а и ScenarioEngine в следующих задачах

В этой задаче **нет реальной интеграции с SendPulse API**. Только интерфейс + типы + фабрика + тесты на саму абстракцию.

---

## Цель

После выполнения этой задачи:

- Существует `app/providers/base.py` с `MessagingProvider` ABC, `IncomingEvent`, `OutgoingMessage`
- Существует `app/providers/sendpulse.py` со скелетом `SendPulseProvider` (методы кидают `NotImplementedError`)
- Существуют `app/providers/manychat.py` и `app/providers/meta.py` со скелетами
- Существует `app/providers/__init__.py` с фабрикой `get_provider()`
- Существует `tests/fakes/fake_provider.py` с рабочим `FakeProvider` для тестов
- Тесты на сериализацию моделей и фабрику зелёные
- `mypy --strict` проходит на всех новых файлах
- В CLAUDE.md обновлён § 7.1 — модели `IncomingEvent` и `OutgoingMessage` указаны как Pydantic, не dataclass

---

## Подзадачи

### 1. Pydantic-модели событий

a) Создать `app/models/events.py`:

```python
"""Pydantic models for events flowing through the system.

These models are the **lingua franca** between MessagingProvider implementations
and the ScenarioEngine. Every provider must produce IncomingEvent and accept
OutgoingMessage. The internal logic does not depend on which provider is active.

Why Pydantic, not @dataclass:
- These models are JSON-serialized into the arq queue
- We want runtime validation of platform / direction / event_type literals
- Pydantic auto-generates JSON schema for OpenAPI docs in admin endpoints
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import EventType, Platform, ProviderName


class IncomingEvent(BaseModel):
    """A normalized event received from any messaging platform.

    Producers: MessagingProvider implementations (parse_webhook).
    Consumers: ScenarioEngine, message workers.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    provider: ProviderName
    platform: Platform
    event_type: EventType
    external_user_id: str = Field(min_length=1, max_length=255)
    external_event_id: str = Field(min_length=1, max_length=255)
    username: str | None = None
    full_name: str | None = None
    text: str | None = None
    media_url: str | None = None
    post_id: str | None = None         # set when event_type='comment'
    comment_id: str | None = None      # set when event_type='comment'
    occurred_at: datetime
    raw_payload: dict[str, Any] = Field(default_factory=dict)


class QuickReply(BaseModel):
    """A button that appears under a message and submits a payload when tapped."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    title: str = Field(min_length=1, max_length=20)   # platform display limits
    payload: str = Field(min_length=1, max_length=1000)


class OutgoingMessage(BaseModel):
    """A message to be sent via a MessagingProvider.

    Producers: ScenarioEngine.
    Consumers: MessagingProvider implementations (send).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    platform: Platform
    external_user_id: str = Field(min_length=1, max_length=255)
    text: str | None = None
    quick_replies: list[QuickReply] | None = None
    media_url: str | None = None
    reply_to_comment_id: str | None = None  # if set, this is a comment private-reply
    scenario_id: int | None = None          # for tracking, optional
```

### 2. MessagingProvider ABC

a) Заменить содержимое `app/providers/base.py` (сейчас там пустой комментарий) на:

```python
"""Abstract base class for all messaging-platform integrations.

Each platform integration (SendPulse, Manychat, Meta) implements this interface.
The rest of the application talks ONLY through this abstraction —
no direct calls to platform-specific SDKs in scenario_engine, claude_responder, etc.

Lifecycle:
1. Webhook arrives → endpoint passes raw bytes + headers to provider.parse_webhook()
2. Provider validates signature, parses JSON, returns list[IncomingEvent]
3. Endpoint enqueues each IncomingEvent into arq queue
4. Worker pulls from queue → ScenarioEngine processes → produces OutgoingMessage
5. Worker calls provider.send(OutgoingMessage)
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from app.models.enums import Platform, ProviderName
from app.models.events import IncomingEvent, OutgoingMessage


class MessagingProvider(ABC):
    """Interface for messaging platform integration."""

    name: ProviderName  # subclasses MUST override with a literal value

    @abstractmethod
    async def parse_webhook(
        self,
        raw_body: bytes,
        headers: dict[str, str],
    ) -> list[IncomingEvent]:
        """Parse and normalize an incoming webhook payload.

        Contract:
        - Returns empty list if signature is invalid (does NOT raise).
          The caller (webhook endpoint) will log and return 200 OK regardless.
        - Returns empty list for non-event pings (e.g. health check from provider).
        - Returns one or more IncomingEvent for actual events.
        - MUST be idempotent: calling twice with same payload → same result.

        Why no raise on invalid signature: webhook endpoint must always return 200,
        otherwise the provider may mark our endpoint as broken and stop sending events.
        """
        ...

    @abstractmethod
    async def send(self, msg: OutgoingMessage) -> str | None:
        """Send a message via the platform's API.

        Returns:
        - external_message_id (str) on success — the platform's ID for this message,
          stored in messages.external_message_id for idempotency.
        - None on failure. Caller may retry via arq's retry mechanism.

        Failures should be logged inside the implementation, not raised.
        Exceptions are reserved for unrecoverable bugs (programmer error).
        """
        ...

    @abstractmethod
    async def fetch_user_profile(
        self,
        platform: Platform,
        external_user_id: str,
    ) -> dict[str, Any]:
        """Fetch a user's public profile (username, full_name, avatar URL).

        Returns a dict with optional keys: 'username', 'full_name', 'profile_pic_url'.
        Returns empty dict {} if the platform doesn't expose this info or call fails.

        This is OPTIONAL — many flows don't need profile lookup.
        ScenarioEngine should not depend on the result being non-empty.
        """
        ...
```

### 3. Skeleton SendPulseProvider

a) Создать `app/providers/sendpulse.py`:

```python
"""SendPulse messaging provider — skeleton.

Full implementation of API methods is in Task 05.
This file establishes the class structure so that get_provider() can
return an instance and the rest of the wiring works.
"""
from __future__ import annotations

from typing import Any, ClassVar

from app.models.enums import Platform, ProviderName
from app.models.events import IncomingEvent, OutgoingMessage
from app.providers.base import MessagingProvider
from app.utils.logging import get_logger

log = get_logger(__name__)


class SendPulseProvider(MessagingProvider):
    """Implementation of MessagingProvider for SendPulse.

    Documentation: https://sendpulse.com/integrations/api/chatbot/instagram

    Auth: OAuth2 client_credentials (CLIENT_ID + CLIENT_SECRET → bearer token).
    Token cached in Redis with TTL of 50 minutes (provider issues 60-min tokens).

    Webhook signature: SendPulse signs webhooks via HMAC-SHA256 over the body
    using the chatbot's secret. Header: `X-Signature` (verify in parse_webhook).
    """

    name: ClassVar[ProviderName] = "sendpulse"

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        webhook_secret: str,
    ) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._webhook_secret = webhook_secret

    async def parse_webhook(
        self,
        raw_body: bytes,
        headers: dict[str, str],
    ) -> list[IncomingEvent]:
        # Implemented in Task 05
        raise NotImplementedError("SendPulseProvider.parse_webhook — Task 05")

    async def send(self, msg: OutgoingMessage) -> str | None:
        # Implemented in Task 05
        raise NotImplementedError("SendPulseProvider.send — Task 05")

    async def fetch_user_profile(
        self,
        platform: Platform,
        external_user_id: str,
    ) -> dict[str, Any]:
        # Implemented in Task 05
        raise NotImplementedError("SendPulseProvider.fetch_user_profile — Task 05")
```

### 4. Заглушки для Manychat и Meta

a) Создать `app/providers/manychat.py`:

```python
"""Manychat messaging provider — placeholder.

Will be implemented if/when we migrate from SendPulse to Manychat
(e.g. when SendPulse pricing becomes prohibitive or features prove insufficient).

For now, this class exists only so MESSAGING_PROVIDER=manychat in .env
fails with a clear error rather than ImportError.
"""
from __future__ import annotations

from typing import Any, ClassVar

from app.models.enums import Platform, ProviderName
from app.models.events import IncomingEvent, OutgoingMessage
from app.providers.base import MessagingProvider


class ManychatProvider(MessagingProvider):
    """Placeholder for future Manychat integration."""

    name: ClassVar[ProviderName] = "manychat"

    def __init__(self) -> None:
        raise NotImplementedError(
            "ManychatProvider not yet implemented. "
            "If you need it, plan a dedicated task and update CLAUDE.md."
        )

    async def parse_webhook(
        self, raw_body: bytes, headers: dict[str, str],
    ) -> list[IncomingEvent]:
        raise NotImplementedError

    async def send(self, msg: OutgoingMessage) -> str | None:
        raise NotImplementedError

    async def fetch_user_profile(
        self, platform: Platform, external_user_id: str,
    ) -> dict[str, Any]:
        raise NotImplementedError
```

b) Создать `app/providers/meta.py`:

```python
"""Meta messaging provider — placeholder.

Will be implemented when we have a verified business entity and a Meta App
with approved permissions (instagram_business_manage_messages, etc.).
See docs/meta_app_review_guide.md for the prerequisites.

Direct integration with Meta Graph API gives us full control and removes
the SendPulse middleman, but requires significant compliance setup.
"""
from __future__ import annotations

from typing import Any, ClassVar

from app.models.enums import Platform, ProviderName
from app.models.events import IncomingEvent, OutgoingMessage
from app.providers.base import MessagingProvider


class MetaProvider(MessagingProvider):
    """Placeholder for future direct Meta Graph API integration."""

    name: ClassVar[ProviderName] = "meta"

    def __init__(self) -> None:
        raise NotImplementedError(
            "MetaProvider requires a verified business entity in Meta App Review. "
            "See docs/meta_app_review_guide.md before enabling."
        )

    async def parse_webhook(
        self, raw_body: bytes, headers: dict[str, str],
    ) -> list[IncomingEvent]:
        raise NotImplementedError

    async def send(self, msg: OutgoingMessage) -> str | None:
        raise NotImplementedError

    async def fetch_user_profile(
        self, platform: Platform, external_user_id: str,
    ) -> dict[str, Any]:
        raise NotImplementedError
```

### 5. Provider factory

a) Заменить содержимое `app/providers/__init__.py` на:

```python
"""Provider factory.

Returns a singleton MessagingProvider instance based on configuration.
Reads config once at first call; subsequent calls return the cached instance.

Usage in FastAPI dependencies:
    from app.providers import get_provider
    provider = get_provider()

In tests, override via monkeypatch on `_singleton` or by calling
reset_provider() (testing-only).
"""
from __future__ import annotations

from app.config import get_settings
from app.providers.base import MessagingProvider
from app.providers.manychat import ManychatProvider
from app.providers.meta import MetaProvider
from app.providers.sendpulse import SendPulseProvider

__all__ = [
    "MessagingProvider",
    "get_provider",
    "reset_provider",
]


_singleton: MessagingProvider | None = None


def get_provider() -> MessagingProvider:
    """Return the configured messaging provider singleton.

    Raises:
        ValueError if MESSAGING_PROVIDER is unknown.
        NotImplementedError if a placeholder provider is selected.
    """
    global _singleton
    if _singleton is not None:
        return _singleton

    settings = get_settings()
    provider_name = settings.messaging_provider

    if provider_name == "sendpulse":
        _singleton = SendPulseProvider(
            client_id=settings.sendpulse_client_id,
            client_secret=settings.sendpulse_client_secret,
            webhook_secret=settings.sendpulse_webhook_secret,
        )
    elif provider_name == "manychat":
        _singleton = ManychatProvider()
    elif provider_name == "meta":
        _singleton = MetaProvider()
    else:
        # Unreachable — pydantic-settings validates the literal — but defensive.
        raise ValueError(f"Unknown messaging provider: {provider_name!r}")

    return _singleton


def reset_provider() -> None:
    """Reset the cached singleton. Tests-only."""
    global _singleton
    _singleton = None
```

### 6. FakeProvider для тестов

a) Создать `tests/fakes/__init__.py` (пустой).

b) Создать `tests/fakes/fake_provider.py`:

```python
"""In-memory fake provider for tests.

Use as a drop-in replacement for SendPulseProvider in tests of
webhook handlers, ScenarioEngine, and end-to-end flows.

Captures all sent messages in `sent` for assertions.
Lets tests control what parse_webhook returns via `queued_events`.
"""
from __future__ import annotations

from typing import Any, ClassVar

from app.models.enums import Platform, ProviderName
from app.models.events import IncomingEvent, OutgoingMessage
from app.providers.base import MessagingProvider


class FakeProvider(MessagingProvider):
    """Test double for MessagingProvider."""

    name: ClassVar[ProviderName] = "sendpulse"  # mimic real provider name

    def __init__(self) -> None:
        self.queued_events: list[IncomingEvent] = []
        self.sent: list[OutgoingMessage] = []
        self.profiles: dict[tuple[Platform, str], dict[str, Any]] = {}
        self.send_should_fail: bool = False
        self.signature_valid: bool = True

    async def parse_webhook(
        self,
        raw_body: bytes,
        headers: dict[str, str],
    ) -> list[IncomingEvent]:
        if not self.signature_valid:
            return []
        events = list(self.queued_events)
        self.queued_events.clear()
        return events

    async def send(self, msg: OutgoingMessage) -> str | None:
        if self.send_should_fail:
            return None
        self.sent.append(msg)
        return f"fake_msg_id_{len(self.sent)}"

    async def fetch_user_profile(
        self,
        platform: Platform,
        external_user_id: str,
    ) -> dict[str, Any]:
        return self.profiles.get((platform, external_user_id), {})

    # --- helpers for tests ---

    def queue_event(self, event: IncomingEvent) -> None:
        """Queue an event to be returned on next parse_webhook call."""
        self.queued_events.append(event)

    def set_profile(
        self,
        platform: Platform,
        external_user_id: str,
        profile: dict[str, Any],
    ) -> None:
        """Pre-populate a profile for fetch_user_profile to return."""
        self.profiles[(platform, external_user_id)] = profile

    def reset(self) -> None:
        """Clear all internal state. Call between tests."""
        self.queued_events.clear()
        self.sent.clear()
        self.profiles.clear()
        self.send_should_fail = False
        self.signature_valid = True
```

### 7. Тесты

a) Создать `tests/test_models_events.py`:

```python
"""Tests for IncomingEvent / OutgoingMessage Pydantic models."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.models.events import IncomingEvent, OutgoingMessage, QuickReply


def test_incoming_event_minimal_fields() -> None:
    event = IncomingEvent(
        provider="sendpulse",
        platform="instagram",
        event_type="message",
        external_user_id="user_123",
        external_event_id="evt_abc",
        occurred_at=datetime(2026, 4, 30, 12, 0, tzinfo=UTC),
    )
    assert event.text is None
    assert event.raw_payload == {}


def test_incoming_event_rejects_unknown_platform() -> None:
    with pytest.raises(ValidationError):
        IncomingEvent(
            provider="sendpulse",
            platform="tiktok",  # type: ignore[arg-type]
            event_type="message",
            external_user_id="u",
            external_event_id="e",
            occurred_at=datetime.now(UTC),
        )


def test_incoming_event_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        IncomingEvent(
            provider="sendpulse",
            platform="instagram",
            event_type="message",
            external_user_id="u",
            external_event_id="e",
            occurred_at=datetime.now(UTC),
            unexpected_field="x",  # type: ignore[call-arg]
        )


def test_incoming_event_rejects_empty_external_user_id() -> None:
    with pytest.raises(ValidationError):
        IncomingEvent(
            provider="sendpulse",
            platform="instagram",
            event_type="message",
            external_user_id="",
            external_event_id="e",
            occurred_at=datetime.now(UTC),
        )


def test_incoming_event_is_frozen() -> None:
    event = IncomingEvent(
        provider="sendpulse",
        platform="instagram",
        event_type="message",
        external_user_id="u",
        external_event_id="e",
        occurred_at=datetime.now(UTC),
    )
    with pytest.raises(ValidationError):
        event.text = "modified"  # type: ignore[misc]


def test_outgoing_message_with_quick_replies() -> None:
    msg = OutgoingMessage(
        platform="instagram",
        external_user_id="user_1",
        text="Привет!",
        quick_replies=[
            QuickReply(title="Очищение", payload="purify"),
            QuickReply(title="Масла", payload="oils"),
        ],
    )
    assert len(msg.quick_replies or []) == 2


def test_quick_reply_title_max_length() -> None:
    with pytest.raises(ValidationError):
        QuickReply(title="x" * 21, payload="p")


def test_outgoing_message_serializes_to_json() -> None:
    """Critical: messages are JSON-serialized into the arq queue."""
    msg = OutgoingMessage(
        platform="facebook",
        external_user_id="u_1",
        text="Hello",
    )
    json_str = msg.model_dump_json()
    assert "facebook" in json_str

    restored = OutgoingMessage.model_validate_json(json_str)
    assert restored == msg


def test_incoming_event_serializes_with_datetime() -> None:
    event = IncomingEvent(
        provider="sendpulse",
        platform="instagram",
        event_type="comment",
        external_user_id="u_1",
        external_event_id="e_1",
        post_id="post_42",
        comment_id="comment_99",
        text="ОЧИЩЕНИЕ",
        occurred_at=datetime(2026, 4, 30, 12, 0, tzinfo=UTC),
    )
    data = event.model_dump(mode="json")
    assert data["occurred_at"] == "2026-04-30T12:00:00Z"
    restored = IncomingEvent.model_validate(data)
    assert restored == event
```

b) Создать `tests/test_provider_factory.py`:

```python
"""Tests for app.providers.get_provider() factory."""
from __future__ import annotations

import pytest

from app.config import get_settings
from app.providers import get_provider, reset_provider
from app.providers.sendpulse import SendPulseProvider


@pytest.fixture(autouse=True)
def _reset() -> None:
    """Reset singleton before each test."""
    reset_provider()
    yield
    reset_provider()


def test_factory_returns_sendpulse_by_default() -> None:
    """Default config (set in conftest.py) is sendpulse."""
    provider = get_provider()
    assert isinstance(provider, SendPulseProvider)
    assert provider.name == "sendpulse"


def test_factory_returns_singleton() -> None:
    p1 = get_provider()
    p2 = get_provider()
    assert p1 is p2


def test_factory_manychat_raises_not_implemented(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MESSAGING_PROVIDER", "manychat")
    get_settings.cache_clear()
    reset_provider()
    with pytest.raises(NotImplementedError, match="ManychatProvider"):
        get_provider()


def test_factory_meta_raises_not_implemented(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MESSAGING_PROVIDER", "meta")
    get_settings.cache_clear()
    reset_provider()
    with pytest.raises(NotImplementedError, match="MetaProvider"):
        get_provider()
```

c) Создать `tests/test_fake_provider.py`:

```python
"""Tests for FakeProvider — the test double used by other tests.

We test the test double itself to make sure later tests can rely on it.
"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.models.events import IncomingEvent, OutgoingMessage
from tests.fakes.fake_provider import FakeProvider


def _make_event() -> IncomingEvent:
    return IncomingEvent(
        provider="sendpulse",
        platform="instagram",
        event_type="message",
        external_user_id="user_1",
        external_event_id="evt_1",
        text="Hello",
        occurred_at=datetime.now(UTC),
    )


@pytest.mark.asyncio
async def test_parse_webhook_returns_queued_events() -> None:
    fake = FakeProvider()
    event = _make_event()
    fake.queue_event(event)

    result = await fake.parse_webhook(b"body", {})
    assert result == [event]

    # Queue should be drained
    assert await fake.parse_webhook(b"body", {}) == []


@pytest.mark.asyncio
async def test_parse_webhook_empty_when_signature_invalid() -> None:
    fake = FakeProvider()
    fake.queue_event(_make_event())
    fake.signature_valid = False

    assert await fake.parse_webhook(b"body", {}) == []


@pytest.mark.asyncio
async def test_send_captures_messages() -> None:
    fake = FakeProvider()
    msg = OutgoingMessage(
        platform="instagram", external_user_id="u_1", text="Hi",
    )
    msg_id = await fake.send(msg)
    assert msg_id == "fake_msg_id_1"
    assert fake.sent == [msg]


@pytest.mark.asyncio
async def test_send_returns_none_on_failure() -> None:
    fake = FakeProvider()
    fake.send_should_fail = True

    msg = OutgoingMessage(
        platform="instagram", external_user_id="u_1", text="Hi",
    )
    result = await fake.send(msg)
    assert result is None
    assert fake.sent == []


@pytest.mark.asyncio
async def test_fetch_user_profile_returns_set_value() -> None:
    fake = FakeProvider()
    fake.set_profile("instagram", "u_1", {"username": "alice", "full_name": "Alice"})

    result = await fake.fetch_user_profile("instagram", "u_1")
    assert result == {"username": "alice", "full_name": "Alice"}


@pytest.mark.asyncio
async def test_fetch_user_profile_returns_empty_when_unset() -> None:
    fake = FakeProvider()
    assert await fake.fetch_user_profile("instagram", "unknown") == {}


def test_reset_clears_state() -> None:
    fake = FakeProvider()
    fake.queue_event(_make_event())
    fake.sent.append(OutgoingMessage(
        platform="instagram", external_user_id="u_1", text="x",
    ))
    fake.reset()
    assert fake.queued_events == []
    assert fake.sent == []
```

### 8. Обновление CLAUDE.md

a) В `CLAUDE.md` найти § 7.1 «Базовый интерфейс» и заменить его описание моделей.

   Старый текст:
   ```
   `app/providers/base.py`:

   ```python
   from __future__ import annotations
   from abc import ABC, abstractmethod
   from dataclasses import dataclass
   ...
   @dataclass(frozen=True)
   class IncomingEvent:
   ```

   Новый текст:

```markdown
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
```

b) В § 4 «Архитектура» обновить блок про worker — заменить упоминание dataclass на упоминание Pydantic для моделей.

c) Добавить запись в § 19 «Что меняется vs CLAUDE.md v1» (если этого ещё нет от предыдущих апдейтов):

```markdown
- **Уточняется:** IncomingEvent и OutgoingMessage — Pydantic v2 модели
  (было: dataclass). Детали в § 7.1.
```

---

## Acceptance criteria

После выполнения всех подзадач выполнить и поставить галочки:

- [ ] Файлы созданы по структуре подзадачи 1–6
- [ ] `make lint` проходит без ошибок (особенно mypy на новых файлах)
- [ ] `make test` проходит, все тесты зелёные:
  - `test_models_events.py` — 9 тестов
  - `test_provider_factory.py` — 4 теста
  - `test_fake_provider.py` — 7 тестов
  - Существующие тесты из Tasks 01 и 03 продолжают работать
- [ ] `python -c "from app.providers import get_provider; p = get_provider(); print(p.name)"` выводит `sendpulse`
- [ ] `python -c "from app.providers import get_provider; p = get_provider(); import asyncio; asyncio.run(p.send(__import__('app.models.events', fromlist=['OutgoingMessage']).OutgoingMessage(platform='instagram', external_user_id='u', text='x')))"` падает с `NotImplementedError: SendPulseProvider.send — Task 05` (это ожидаемо — реализация в следующей задаче)
- [ ] `MESSAGING_PROVIDER=manychat` в `.env` приводит к понятной ошибке `ManychatProvider not yet implemented` при запуске
- [ ] `MESSAGING_PROVIDER=meta` в `.env` приводит к ошибке про `meta_app_review_guide.md`
- [ ] Pydantic-модели IncomingEvent и OutgoingMessage сериализуются в JSON и десериализуются обратно без потерь
- [ ] CLAUDE.md обновлён в § 7.1 — упоминание Pydantic вместо dataclass
- [ ] Сигнатуры `parse_webhook` / `send` / `fetch_user_profile` совпадают между ABC и всеми реализациями (mypy это проверит)

---

## Do NOT

- НЕ начинать реализацию реальных вызовов SendPulse API в этой задаче. Только скелет с `NotImplementedError`. Реальная реализация — Task 05.
- НЕ реализовывать `ManychatProvider` или `MetaProvider`. Только заглушки с `NotImplementedError` в `__init__`.
- НЕ менять структуру `IncomingEvent` или `OutgoingMessage` после создания, чтобы не ломать ABC контракт. Если потребуется поле — отдельная задача.
- НЕ использовать `@dataclass` для моделей событий. Только Pydantic v2.
- НЕ хранить состояние (Redis-сессию, БД-pool) в `_singleton` провайдера. Состояние — в repos/pool.py и других сервисах.
- НЕ возвращать сырые dict-ы из методов. Только типизированные модели или примитивы.
- НЕ добавлять методы в `MessagingProvider` ABC сверх трёх указанных. Если провайдер требует что-то платформо-специфичное (например, SendPulse-only OAuth refresh), это приватные методы конкретной реализации.
- НЕ импортировать `app.providers.sendpulse` напрямую в коде ScenarioEngine или webhook handler'а. Только через `get_provider()`.
- НЕ добавлять зависимости вне списка из Task 01.

---

## Зависимости задачи

- Task 01 применена (есть `pyproject.toml`, структура `app/`)
- Task 03 применена (есть `app/utils/logging.py`, `app/models/enums.py`, `app/config.py`)
- Не требует SendPulse API credentials — это Task 05

---

## Что после этой задачи

После применения Task 04 у нас есть полный фундамент абстракции провайдера. Дальше:

- **Task 05** — SendPulseProvider implementation (требует API credentials от Юли — Task 02)
- **Task 06** — Webhook endpoint + arq worker scaffold (использует `get_provider()` через FastAPI Depends; для тестов подменит на `FakeProvider` через monkeypatch `_singleton`)

---

**Дата создания:** 2026-04-30
**Применять в:** `D:\Work\social_inbox` после Task 03
**Эстимейт:** 2 часа на Claude Code + ручная проверка
