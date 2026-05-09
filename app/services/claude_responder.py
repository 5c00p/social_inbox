"""Claude API wrapper for smart-mode replies.

Responsibilities:
- Build messages array from conversation history
- Wrap user content in <user_message> tags (prompt injection defense)
- Call Anthropic API with tool use enabled
- Detect escalate_to_human tool calls
- Track token usage in Redis budget + DB messages row
- Handle API errors gracefully (return None on failure, never raise to caller)

Returns:
- ClaudeReply with text and metadata on successful response
- ClaudeReply with escalation=True (no text) when Claude requested handover
- None on failure / over-budget / no content (caller should fall back gracefully)
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from anthropic import AsyncAnthropic
from anthropic._exceptions import APIError, APIStatusError

from app.config import get_settings
from app.repos import messages as messages_repo
from app.repos import token_budget
from app.utils.logging import get_logger

if TYPE_CHECKING:
    import asyncpg

log = get_logger(__name__)

PROMPT_PATH = Path(__file__).resolve().parents[1] / "prompts" / "system_smart.md"

# Cache the prompt at module load — it's static, no need to re-read.
_SYSTEM_PROMPT_CACHE: str | None = None


def _load_system_prompt() -> str:
    global _SYSTEM_PROMPT_CACHE
    if _SYSTEM_PROMPT_CACHE is None:
        _SYSTEM_PROMPT_CACHE = PROMPT_PATH.read_text(encoding="utf-8").strip()
    return _SYSTEM_PROMPT_CACHE


# Tool definitions for Claude
TOOLS: list[dict[str, Any]] = [
    {
        "name": "escalate_to_human",
        "description": (
            "Передай разговор живому оператору (Юле). "
            "Используй когда: пользователь жалуется на симптомы/болезнь; "
            "пользователь беременна; вопрос про детей до 6 лет; "
            "пользователь явно просит человека/оператора; "
            "сложный персональный вопрос; пользователь раздражён."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "reason": {
                    "type": "string",
                    "description": "Краткая причина эскалации (1-2 предложения, на русском).",
                }
            },
            "required": ["reason"],
        },
    }
]


@dataclass(frozen=True)
class ClaudeReply:
    """Result of a Claude API call."""

    text: str | None
    escalation: bool
    escalation_reason: str | None
    tokens_in: int
    tokens_out: int
    model: str


_client: AsyncAnthropic | None = None


def _get_client() -> AsyncAnthropic:
    global _client
    if _client is None:
        settings = get_settings()
        _client = AsyncAnthropic(api_key=settings.anthropic_api_key)
    return _client


def reset_client() -> None:
    """Reset the Anthropic client. Tests use this with monkeypatch."""
    global _client
    _client = None


async def respond(
    *,
    user_id: int,
    conversation_id: int,
    incoming_text: str,
    model: str | None = None,
    max_tokens: int = 500,
) -> ClaudeReply | None:
    """Generate a smart reply via Claude API.

    Args:
        user_id: social_users.id, used for token budget tracking
        conversation_id: conversations.id, used to load history
        incoming_text: the latest user message (just inserted into messages table)
        model: Claude model identifier (defaults to settings.claude_default_model)
        max_tokens: cap on output length

    Returns:
        ClaudeReply on success.
        None if budget exceeded, API error, empty response, or no usable content.
    """
    settings = get_settings()
    chosen_model = model or settings.claude_default_model

    # Budget gate
    if not await token_budget.can_call_claude(user_id):
        return None

    # Load conversation context (last 20 messages, chronological)
    history = await messages_repo.get_recent(conversation_id, limit=20)

    # Build Anthropic messages array
    api_messages = _build_messages(history, latest_user_text=incoming_text)
    system_prompt = _load_system_prompt()

    client = _get_client()
    try:
        response = await client.messages.create(
            model=chosen_model,
            max_tokens=max_tokens,
            system=system_prompt,
            tools=TOOLS,  # type: ignore[arg-type]
            messages=api_messages,  # type: ignore[arg-type]
        )
    except APIStatusError as exc:
        log.warning(
            "claude_api_status_error",
            user_id=user_id,
            status=exc.status_code,
            message=str(exc)[:200],
        )
        return None
    except APIError as exc:
        log.warning(
            "claude_api_error",
            user_id=user_id,
            error=str(exc)[:200],
        )
        return None
    except Exception as exc:
        log.exception("claude_unexpected_error", user_id=user_id, error=str(exc))
        return None

    tokens_in = response.usage.input_tokens
    tokens_out = response.usage.output_tokens
    await token_budget.record_usage(user_id, tokens_in, tokens_out)

    # Inspect content blocks
    text_parts: list[str] = []
    escalation_reason: str | None = None
    for block in response.content:
        if block.type == "text":
            text_parts.append(block.text)
        elif block.type == "tool_use" and block.name == "escalate_to_human":
            input_dict = block.input if isinstance(block.input, dict) else {}
            escalation_reason = input_dict.get("reason", "no reason given")

    if escalation_reason is not None:
        log.info(
            "claude_requested_escalation",
            user_id=user_id,
            reason=escalation_reason,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
        )
        return ClaudeReply(
            text=None,
            escalation=True,
            escalation_reason=escalation_reason,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            model=chosen_model,
        )

    text = "\n".join(text_parts).strip()
    if not text:
        log.warning("claude_empty_response", user_id=user_id)
        return None

    log.info(
        "claude_replied",
        user_id=user_id,
        model=chosen_model,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        text_length=len(text),
    )
    return ClaudeReply(
        text=text,
        escalation=False,
        escalation_reason=None,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        model=chosen_model,
    )


def _build_messages(
    history: list[asyncpg.Record],
    latest_user_text: str,
) -> list[dict[str, Any]]:
    """Convert messages-table rows + latest user message into Anthropic API format.

    Format rules:
    - Conversation MUST start with role='user' (Anthropic API requirement)
    - Wrap user-side content in <user_message>...</user_message> for prompt-injection safety
    - Skip messages with NULL text (media-only)
    - The latest user message is appended explicitly (it may not yet be in `history`
      since the worker just inserted it before calling Claude)
    """
    api_messages: list[dict[str, Any]] = []

    for row in history:
        if not row["text"]:
            continue
        if row["direction"] == "in":
            api_messages.append({
                "role": "user",
                "content": f"<user_message>{row['text']}</user_message>",
            })
        else:
            api_messages.append({
                "role": "assistant",
                "content": row["text"],
            })

    # Append latest user message
    api_messages.append({
        "role": "user",
        "content": f"<user_message>{latest_user_text}</user_message>",
    })

    # Anthropic API requires conversation to start with role='user'.
    # If history starts with assistant (e.g. welcome sent before user said anything),
    # drop leading assistant messages.
    while api_messages and api_messages[0]["role"] != "user":
        api_messages.pop(0)

    return api_messages
