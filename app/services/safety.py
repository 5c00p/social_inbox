"""Safety filters for incoming and outgoing messages.

Two independent checks:

1. check_outgoing(text):
   Scans an outgoing reply (typically Claude-generated) for banned patterns.
   Used by smart scenario AFTER Claude returns text, BEFORE forwarding to provider.
   If matched: message is NOT sent, conversation goes to handover_pending,
   audit row written to messages with safety_blocked=True.

2. check_incoming(text):
   Quick triage of incoming messages. Detects:
   - Operator-request keywords ("оператор", "human") — explicit handover
   - Symptom keywords ("болит", "диагноз") — pre-emptive handover
     (cheaper than letting Claude tool-call escalate; protects against rare
     cases when Claude misjudges a medical question)
   Used by worker BEFORE scenario engine.

Trusted vs untrusted templates:
- Welcome / comment-to-DM templates from `scenarios.template` are TRUSTED:
  written by humans, reviewed, change rarely. NOT subjected to check_outgoing.
- Claude smart replies are UNTRUSTED: subject to check_outgoing.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.prompts.banned_patterns import (
    BANNED_PATTERNS,
    OPERATOR_KEYWORDS,
    SYMPTOM_KEYWORDS,
)
from app.utils.logging import get_logger

log = get_logger(__name__)

OutgoingVerdict = Literal["ok", "blocked"]
IncomingTrigger = Literal["none", "operator_request", "symptom"]


@dataclass(frozen=True)
class OutgoingCheck:
    verdict: OutgoingVerdict
    reason: str | None  # filled when verdict='blocked'


@dataclass(frozen=True)
class IncomingCheck:
    trigger: IncomingTrigger
    matched_text: str | None  # the snippet that triggered, for logging


def check_outgoing(text: str) -> OutgoingCheck:
    """Scan an outgoing reply for banned patterns.

    Returns:
        OutgoingCheck(verdict='ok') if clean
        OutgoingCheck(verdict='blocked', reason=label) on first match
    """
    if not text:
        return OutgoingCheck(verdict="ok", reason=None)
    for bp in BANNED_PATTERNS:
        if bp.pattern.search(text):
            log.warning(
                "safety_outgoing_blocked",
                pattern_label=bp.label,
                text_preview=text[:100],
            )
            return OutgoingCheck(verdict="blocked", reason=bp.label)
    return OutgoingCheck(verdict="ok", reason=None)


def check_incoming(text: str | None) -> IncomingCheck:
    """Triage an incoming message: does it require pre-emptive handover?

    Order of priority:
    1. Operator-request keyword → handover with explicit user-facing reply
    2. Symptom keyword → handover, optional silent acknowledgement
    3. None → let scenario engine route normally
    """
    if not text:
        return IncomingCheck(trigger="none", matched_text=None)

    for pattern in OPERATOR_KEYWORDS:
        m = pattern.search(text)
        if m:
            return IncomingCheck(trigger="operator_request", matched_text=m.group(0))

    for pattern in SYMPTOM_KEYWORDS:
        m = pattern.search(text)
        if m:
            return IncomingCheck(trigger="symptom", matched_text=m.group(0))

    return IncomingCheck(trigger="none", matched_text=None)
