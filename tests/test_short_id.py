"""Tests for app.utils.short_id."""
from __future__ import annotations

from app.utils.short_id import LENGTH, make_short_id


def test_short_id_length() -> None:
    assert len(make_short_id()) == LENGTH


def test_short_id_alphabet() -> None:
    sid = make_short_id()
    assert "_" not in sid
    assert "-" not in sid
    assert all(c.isalnum() for c in sid)


def test_short_ids_are_unique() -> None:
    ids = {make_short_id() for _ in range(1000)}
    assert len(ids) == 1000  # collisions extremely unlikely at 8 chars
