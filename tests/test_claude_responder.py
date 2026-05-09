"""Tests for ClaudeResponder.

We mock the Anthropic SDK at the module-level _client to avoid real API calls.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.repos import conversations, messages, users
from app.services import claude_responder
from app.services.claude_responder import reset_client


@dataclass
class _FakeUsage:
    input_tokens: int
    output_tokens: int


@dataclass
class _FakeContentText:
    type: str = "text"
    text: str = ""


@dataclass
class _FakeContentToolUse:
    type: str = "tool_use"
    name: str = ""
    input: dict[str, Any] | None = None


@dataclass
class _FakeResponse:
    content: list[Any]
    usage: _FakeUsage


def _install_fake_client(monkeypatch: pytest.MonkeyPatch, response: _FakeResponse) -> MagicMock:
    """Replace _client in claude_responder with a mock that returns `response`."""
    fake_client = MagicMock()
    fake_messages = MagicMock()
    fake_messages.create = AsyncMock(return_value=response)
    fake_client.messages = fake_messages
    monkeypatch.setattr(claude_responder, "_client", fake_client)
    return fake_client


@pytest.fixture(autouse=True)
def _reset_responder_client() -> None:  # type: ignore[misc]
    reset_client()
    yield  # type: ignore[misc]
    reset_client()


async def _setup_user_and_conv(db: Any, external_id: str = "claude_user_1") -> tuple[Any, Any]:
    user = await users.create(
        provider_name="sendpulse",
        platform="instagram",
        external_id=external_id,
    )
    conv = await conversations.create(user["id"], "instagram")
    return user, conv


async def test_respond_returns_text_on_success(
    db: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    user, conv = await _setup_user_and_conv(db, "claude_ok")
    fake_response = _FakeResponse(
        content=[_FakeContentText(text="Конечно расскажу! 🌿")],
        usage=_FakeUsage(input_tokens=100, output_tokens=20),
    )
    _install_fake_client(monkeypatch, fake_response)

    reply = await claude_responder.respond(
        user_id=user["id"],
        conversation_id=conv["id"],
        incoming_text="Расскажи про очищение",
    )

    assert reply is not None
    assert reply.text == "Конечно расскажу! 🌿"
    assert reply.escalation is False
    assert reply.tokens_in == 100
    assert reply.tokens_out == 20


async def test_respond_detects_escalation(
    db: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    user, conv = await _setup_user_and_conv(db, "claude_esc")
    fake_response = _FakeResponse(
        content=[
            _FakeContentToolUse(
                name="escalate_to_human",
                input={"reason": "Пользователь спрашивает про симптомы"},
            ),
        ],
        usage=_FakeUsage(input_tokens=80, output_tokens=15),
    )
    _install_fake_client(monkeypatch, fake_response)

    reply = await claude_responder.respond(
        user_id=user["id"],
        conversation_id=conv["id"],
        incoming_text="У меня болит голова, что использовать?",
    )

    assert reply is not None
    assert reply.escalation is True
    assert reply.escalation_reason is not None
    assert "симптом" in reply.escalation_reason.lower()
    assert reply.text is None


async def test_respond_returns_none_on_empty_content(
    db: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    user, conv = await _setup_user_and_conv(db, "claude_empty")
    fake_response = _FakeResponse(
        content=[_FakeContentText(text="")],
        usage=_FakeUsage(input_tokens=10, output_tokens=0),
    )
    _install_fake_client(monkeypatch, fake_response)

    reply = await claude_responder.respond(
        user_id=user["id"],
        conversation_id=conv["id"],
        incoming_text="hi",
    )
    assert reply is None


async def test_respond_returns_none_on_api_error(
    db: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from anthropic._exceptions import APIError

    user, conv = await _setup_user_and_conv(db, "claude_err")
    fake_client = MagicMock()
    fake_messages = MagicMock()
    fake_messages.create = AsyncMock(side_effect=APIError(
        message="boom", request=MagicMock(), body=None,
    ))
    fake_client.messages = fake_messages
    monkeypatch.setattr(claude_responder, "_client", fake_client)

    reply = await claude_responder.respond(
        user_id=user["id"],
        conversation_id=conv["id"],
        incoming_text="hi",
    )
    assert reply is None


async def test_respond_blocks_on_budget_exceeded(
    db: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.repos import token_budget

    user, conv = await _setup_user_and_conv(db, "claude_budget")
    # Saturate budget
    await token_budget.record_usage(
        user["id"],
        tokens_in=token_budget.INPUT_BUDGET_PER_DAY,
        tokens_out=0,
    )

    # Even though API would succeed, we should not call it
    create_mock = AsyncMock()
    fake_client = MagicMock()
    fake_messages = MagicMock()
    fake_messages.create = create_mock
    fake_client.messages = fake_messages
    monkeypatch.setattr(claude_responder, "_client", fake_client)

    reply = await claude_responder.respond(
        user_id=user["id"],
        conversation_id=conv["id"],
        incoming_text="hi",
    )

    assert reply is None
    create_mock.assert_not_called()


async def test_build_messages_wraps_user_in_xml(db: Any) -> None:
    user, conv = await _setup_user_and_conv(db, "claude_xml")
    # Insert one previous turn
    await messages.insert(
        conversation_id=conv["id"],
        direction="in",
        text="Hi there",
        external_message_id="hist_1",
    )
    await messages.insert(
        conversation_id=conv["id"],
        direction="out",
        text="Hello!",
        external_message_id="hist_2",
    )
    history = await messages.get_recent(conv["id"], limit=20)

    api_msgs = claude_responder._build_messages(history, latest_user_text="What about oils?")

    # Last message is the new user message, wrapped
    assert api_msgs[-1]["role"] == "user"
    assert "<user_message>What about oils?</user_message>" in api_msgs[-1]["content"]

    # Earlier user message also wrapped
    user_msgs = [m for m in api_msgs if m["role"] == "user"]
    assert all("<user_message>" in m["content"] for m in user_msgs)

    # Assistant message NOT wrapped
    asst_msgs = [m for m in api_msgs if m["role"] == "assistant"]
    assert all("<user_message>" not in m["content"] for m in asst_msgs)


async def test_records_token_usage_after_call(
    db: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.repos import token_budget
    from app.repos.redis_client import get_redis

    user, conv = await _setup_user_and_conv(db, "claude_usage")
    redis = await get_redis()
    await redis.delete(token_budget._input_key(user["id"]))
    await redis.delete(token_budget._output_key(user["id"]))

    fake_response = _FakeResponse(
        content=[_FakeContentText(text="answer")],
        usage=_FakeUsage(input_tokens=250, output_tokens=80),
    )
    _install_fake_client(monkeypatch, fake_response)

    await claude_responder.respond(
        user_id=user["id"],
        conversation_id=conv["id"],
        incoming_text="q",
    )

    in_used = int(await redis.get(token_budget._input_key(user["id"])) or 0)
    out_used = int(await redis.get(token_budget._output_key(user["id"])) or 0)
    assert in_used == 250
    assert out_used == 80
