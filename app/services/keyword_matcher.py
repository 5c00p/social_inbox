"""Match a text against keywords loaded from DB.

Strategy:
- Cache keywords in memory for 60 seconds (avoid DB hit on every message)
- Sort by (priority asc, id asc) — first match wins
- Three match types: exact, contains, regex
- case_sensitive flag is per-keyword

Used by ScenarioEngine to decide which scenario (if any) to trigger.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Literal

from app.repos import keywords as keywords_repo
from app.utils.logging import get_logger

log = get_logger(__name__)

CACHE_TTL_SECONDS = 60.0

KeywordContext = Literal["dm", "comment", "both"]
MatchType = Literal["exact", "contains", "regex"]


@dataclass(frozen=True)
class KeywordMatch:
    """Result of a successful match."""

    keyword_id: int
    keyword: str
    scenario_id: int
    matched_text: str


@dataclass(frozen=True)
class _Compiled:
    """A keyword pre-compiled for fast matching."""

    keyword_id: int
    keyword: str
    match_type: MatchType
    scenario_id: int
    priority: int
    case_sensitive: bool
    regex: re.Pattern[str] | None  # only set when match_type='regex'


# Module-level cache. Reset via reset_cache() in tests.
_cache: dict[KeywordContext, list[_Compiled]] = {}
_cache_loaded_at: dict[KeywordContext, float] = {}


async def _load(context: KeywordContext) -> list[_Compiled]:
    """Load keywords from DB and compile patterns."""
    rows = await keywords_repo.list_active(context)
    compiled: list[_Compiled] = []
    for row in rows:
        regex: re.Pattern[str] | None = None
        if row["match_type"] == "regex":
            try:
                flags = 0 if row["case_sensitive"] else re.IGNORECASE
                regex = re.compile(row["keyword"], flags)
            except re.error as exc:
                log.warning(
                    "keyword_regex_invalid",
                    keyword_id=row["id"],
                    keyword=row["keyword"],
                    error=str(exc),
                )
                continue
        compiled.append(_Compiled(
            keyword_id=row["id"],
            keyword=row["keyword"],
            match_type=row["match_type"],
            scenario_id=row["scenario_id"],
            priority=row["priority"],
            case_sensitive=row["case_sensitive"],
            regex=regex,
        ))
    return compiled


async def _get_or_load(context: KeywordContext) -> list[_Compiled]:
    """Return cached keywords or re-load if cache expired."""
    now = time.monotonic()
    loaded_at = _cache_loaded_at.get(context, 0.0)
    if context in _cache and (now - loaded_at) < CACHE_TTL_SECONDS:
        return _cache[context]
    compiled = await _load(context)
    _cache[context] = compiled
    _cache_loaded_at[context] = now
    log.debug("keyword_cache_refreshed", context=context, count=len(compiled))
    return compiled


def _matches(c: _Compiled, text: str) -> bool:
    """Check if a single compiled keyword matches the text."""
    if c.match_type == "regex":
        assert c.regex is not None
        return bool(c.regex.search(text))

    target = text if c.case_sensitive else text.lower()
    needle = c.keyword if c.case_sensitive else c.keyword.lower()

    if c.match_type == "exact":
        return target.strip() == needle
    if c.match_type == "contains":
        return needle in target
    return False


async def match(text: str, context: KeywordContext) -> KeywordMatch | None:
    """Return first matching keyword (by priority) or None.

    Empty/None text returns None — keyword matching only applies to text events.
    """
    if not text:
        return None
    compiled = await _get_or_load(context)
    for c in compiled:
        if _matches(c, text):
            return KeywordMatch(
                keyword_id=c.keyword_id,
                keyword=c.keyword,
                scenario_id=c.scenario_id,
                matched_text=text,
            )
    return None


def reset_cache() -> None:
    """Reset in-memory cache. Tests-only."""
    _cache.clear()
    _cache_loaded_at.clear()
