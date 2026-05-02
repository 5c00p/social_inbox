"""Tests for KeywordMatcher."""
from __future__ import annotations

import pytest

from app.services.keyword_matcher import match, reset_cache


@pytest.fixture(autouse=True)
def _reset_kw_cache() -> None:
    reset_cache()
    yield  # type: ignore[misc]
    reset_cache()


async def _seed_scenario(db, name: str = "test_scenario", type_: str = "echo") -> int:
    row = await db.fetchrow(
        """
        INSERT INTO scenarios (name, type, active) VALUES ($1, $2, TRUE)
        RETURNING id
        """,
        name, type_,
    )
    return row["id"]  # type: ignore[no-any-return]


async def _seed_keyword(
    db,
    *,
    keyword: str,
    match_type: str,
    context: str,
    scenario_id: int,
    priority: int = 100,
    case_sensitive: bool = False,
) -> int:
    row = await db.fetchrow(
        """
        INSERT INTO keywords (keyword, match_type, context, scenario_id, priority, case_sensitive)
        VALUES ($1, $2, $3, $4, $5, $6)
        RETURNING id
        """,
        keyword, match_type, context, scenario_id, priority, case_sensitive,
    )
    return row["id"]  # type: ignore[no-any-return]


@pytest.mark.asyncio
async def test_exact_match(db) -> None:
    sid = await _seed_scenario(db, "s_exact")
    await _seed_keyword(db, keyword="ОЧИЩЕНИЕ", match_type="exact", context="dm", scenario_id=sid)

    m = await match("ОЧИЩЕНИЕ", "dm")
    assert m is not None
    assert m.scenario_id == sid

    reset_cache()
    m2 = await match("ОЧИЩЕНИЕ программа", "dm")
    assert m2 is None  # exact doesn't match substring


@pytest.mark.asyncio
async def test_contains_match(db) -> None:
    sid = await _seed_scenario(db, "s_contains")
    await _seed_keyword(db, keyword="масла", match_type="contains", context="dm", scenario_id=sid)

    m = await match("Расскажи про масла doTERRA", "dm")
    assert m is not None
    assert m.scenario_id == sid


@pytest.mark.asyncio
async def test_regex_match(db) -> None:
    sid = await _seed_scenario(db, "s_regex")
    await _seed_keyword(
        db, keyword=r"\bпробник\w*\b", match_type="regex",
        context="dm", scenario_id=sid,
    )

    m = await match("Хочу пробники", "dm")
    assert m is not None
    assert m.scenario_id == sid


@pytest.mark.asyncio
async def test_priority_ordering(db) -> None:
    s_low = await _seed_scenario(db, "s_priority_low")
    s_high = await _seed_scenario(db, "s_priority_high")
    # priority=10 (higher prio: lower number)
    await _seed_keyword(
        db, keyword="hello", match_type="contains",
        context="dm", scenario_id=s_high, priority=10,
    )
    # priority=100 (lower prio)
    await _seed_keyword(
        db, keyword="hello", match_type="contains",
        context="dm", scenario_id=s_low, priority=100,
    )

    m = await match("hello world", "dm")
    assert m is not None
    assert m.scenario_id == s_high  # higher priority wins


@pytest.mark.asyncio
async def test_case_insensitive_default(db) -> None:
    sid = await _seed_scenario(db, "s_case")
    await _seed_keyword(
        db, keyword="ОЧИЩЕНИЕ", match_type="exact",
        context="dm", scenario_id=sid, case_sensitive=False,
    )

    m = await match("очищение", "dm")
    assert m is not None


@pytest.mark.asyncio
async def test_case_sensitive_strict(db) -> None:
    sid = await _seed_scenario(db, "s_case_strict")
    await _seed_keyword(
        db, keyword="DETOX", match_type="exact",
        context="dm", scenario_id=sid, case_sensitive=True,
    )

    assert await match("detox", "dm") is None
    reset_cache()
    assert await match("DETOX", "dm") is not None


@pytest.mark.asyncio
async def test_context_filter(db) -> None:
    sid = await _seed_scenario(db, "s_ctx")
    await _seed_keyword(
        db, keyword="hi", match_type="exact",
        context="comment", scenario_id=sid,
    )

    assert await match("hi", "dm") is None      # different context
    reset_cache()
    assert await match("hi", "comment") is not None


@pytest.mark.asyncio
async def test_both_context_matches_either(db) -> None:
    sid = await _seed_scenario(db, "s_both")
    await _seed_keyword(
        db, keyword="hi", match_type="exact",
        context="both", scenario_id=sid,
    )

    assert await match("hi", "dm") is not None
    reset_cache()
    assert await match("hi", "comment") is not None


@pytest.mark.asyncio
async def test_invalid_regex_skipped(db) -> None:
    sid = await _seed_scenario(db, "s_bad_re")
    await _seed_keyword(
        db, keyword="[unclosed", match_type="regex",
        context="dm", scenario_id=sid,
    )

    # Doesn't crash, just skips the invalid pattern.
    assert await match("anything", "dm") is None


@pytest.mark.asyncio
async def test_empty_text_returns_none(db) -> None:
    sid = await _seed_scenario(db, "s_empty")
    await _seed_keyword(
        db, keyword="hi", match_type="exact",
        context="dm", scenario_id=sid,
    )

    assert await match("", "dm") is None
    assert await match(None, "dm") is None  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_cache_reused_within_ttl(db) -> None:
    """Second call within TTL doesn't hit DB."""
    sid = await _seed_scenario(db, "s_cache")
    await _seed_keyword(
        db, keyword="cached", match_type="contains",
        context="dm", scenario_id=sid,
    )

    # Prime cache
    m1 = await match("text with cached word", "dm")
    assert m1 is not None

    # Now if we add a NEW keyword without resetting cache, it shouldn't be visible
    sid2 = await _seed_scenario(db, "s_cache_new")
    await _seed_keyword(
        db, keyword="newone", match_type="contains",
        context="dm", scenario_id=sid2,
    )
    m2 = await match("text with newone in it", "dm")
    assert m2 is None  # cached snapshot doesn't include the new keyword
