"""Banned patterns for outgoing messages — doTERRA compliance.

Why a separate module:
- Easy to update without touching service code
- Reviewed by Yulia (and ideally by a doTERRA compliance lawyer)
- Same patterns can be re-used by future scenarios (e.g. admin pre-publish review)

Why regex, not AI:
- Deterministic, fast (<1ms)
- Can be tested exhaustively
- A semantic check (LLM-based) would be expensive and could itself hallucinate
- We use Claude system prompt for the SOFT guidance, regex as the HARD floor

How to add a new pattern:
1. Add to BANNED_PATTERNS with descriptive comment
2. Add a positive test in tests/test_safety.py (a phrase that MUST be blocked)
3. Add a negative test (a similar but allowed phrase that MUST pass)

How to test patterns interactively:
    python -c "from app.services.safety import check_outgoing; print(check_outgoing('ваш текст'))"
"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class BannedPattern:
    """A regex pattern with a human-readable label."""

    pattern: re.Pattern[str]
    label: str  # Short description used in logs and admin notifications


# Compiled at module import. All patterns are case-insensitive.
def _compile(pattern_str: str, label: str) -> BannedPattern:
    return BannedPattern(
        pattern=re.compile(pattern_str, re.IGNORECASE | re.UNICODE),
        label=label,
    )


BANNED_PATTERNS: list[BannedPattern] = [
    # --- Direct medical claims ---
    _compile(r"\bлечит\b",                              "медицинское: лечит"),
    _compile(r"\bвылечит\b",                            "медицинское: вылечит"),
    _compile(r"\bвылечив\w*\b",                         "медицинское: вылечив*"),
    _compile(r"\bизлечив\w*\b",                         "медицинское: излечив*"),
    _compile(r"\bисцел\w*\b",                           "медицинское: исцеляет"),

    # --- Disease prevention claims ---
    _compile(
        r"\bпрофилактик\w*\s+(рак|covid|гриппа|онколог\w*|диабет\w*|инфекци\w*)",
        "медицинское: профилактика конкретного заболевания",
    ),
    _compile(
        r"\bпредотвра\w+\s+(рак|covid|гриппа|онколог\w*|диабет\w*)",
        "медицинское: предотвращает заболевание",
    ),

    # --- Drug-replacement claims ---
    _compile(r"\bантибиотик",                            "медицинское: масла как антибиотики"),
    _compile(r"\bвместо\s+лекарств",                    "медицинское: вместо лекарств"),
    _compile(r"\bвместо\s+таблет\w*",                   "медицинское: вместо таблеток"),
    _compile(r"\bзамен\w+\s+(лекарств|препарат|таблет)", "медицинское: замена лекарств"),
    _compile(r"отмен\w+\s+(лекарств|препарат|таблет)",  "медицинское: отменить лекарства"),
    _compile(r"\bне\s+нужно\s+к\s+врач",                "медицинское: не нужно к врачу"),

    # --- Categorical promises ---
    _compile(r"\bгаранти(?:ру\w+|я)\b",                 "обещание: гарантирую"),
    _compile(r"\b100\s*%\s+результат",                  "обещание: 100% результат"),
    _compile(r"\bточно\s+поможет",                      "обещание: точно поможет"),
    _compile(r"\bобязательно\s+(вылеч|излеч|поможет)",  "обещание: обязательно вылечит/поможет"),

    # --- Diagnosis ---
    _compile(r"\b(у\s+вас|у\s+тебя)\s+(диагноз|симптом)", "диагностика пациента"),

    # --- Self-medication safety ---
    _compile(r"внутрь\s+без\s+консультаци",             "опасное самолечение"),
    _compile(r"\bпринимайте\s+внутрь\b",                "опасное самолечение: принимайте внутрь"),
]


@dataclass(frozen=True)
class SymptomMatch:
    """Marker that the user is reporting symptoms — pre-emptive handover."""

    keyword: str


SYMPTOM_KEYWORDS: list[re.Pattern[str]] = [
    re.compile(r"\bболит\b",                re.IGNORECASE | re.UNICODE),
    re.compile(r"\bболь\s+(в|у)\b",         re.IGNORECASE | re.UNICODE),
    re.compile(r"\bдиагноз\b",              re.IGNORECASE | re.UNICODE),
    re.compile(r"\bврач\b",                 re.IGNORECASE | re.UNICODE),
    re.compile(r"\bбольниц\w*\b",           re.IGNORECASE | re.UNICODE),
    re.compile(r"\bтаблетк\w*\b",           re.IGNORECASE | re.UNICODE),
    re.compile(r"\bлекарств\w*\b",          re.IGNORECASE | re.UNICODE),
    re.compile(r"\bпрепарат\w*\b",          re.IGNORECASE | re.UNICODE),
    re.compile(r"\bбеременн\w+\b",          re.IGNORECASE | re.UNICODE),
    re.compile(r"\bкорм(лю|ит)\s+грудью\b", re.IGNORECASE | re.UNICODE),
    re.compile(r"\bгрудно[йг]\s+ребен",     re.IGNORECASE | re.UNICODE),
]


# Operator-request keywords — explicit handover requests
OPERATOR_KEYWORDS: list[re.Pattern[str]] = [
    re.compile(r"\bоператор\w*\b",              re.IGNORECASE | re.UNICODE),
    re.compile(r"\bадминистратор\w*\b",         re.IGNORECASE | re.UNICODE),
    re.compile(r"(хочу|нужен|позов\w*)\s+человек", re.IGNORECASE | re.UNICODE),
    re.compile(r"(говорить|пообщат\w*)\s+с\s+юл",  re.IGNORECASE | re.UNICODE),
    re.compile(r"с\s+юл\w+\s+\w+\s+пообщат\w*",   re.IGNORECASE | re.UNICODE),
    re.compile(r"\bagent\b",                    re.IGNORECASE),
    re.compile(r"\bhuman\b",                    re.IGNORECASE),
]
