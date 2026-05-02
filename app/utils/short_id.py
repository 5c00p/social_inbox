"""Generate short, URL-safe IDs for use in Telegram deep links.

Format: 8 chars from alphabet [0-9A-Za-z] (no `_` or `-`).
- `_` is the separator in our deep link format `ig_<short_id>_<scenario>`,
  so it MUST NOT appear inside short_id itself.
- `-` looks ugly in URLs.
At 8 chars, alphabet size 62: 218 trillion combinations,
collisions negligible at our scale (<=100k leads in 5-year horizon).
"""
from __future__ import annotations

from nanoid import generate

ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
LENGTH = 8


def make_short_id() -> str:
    """Return a fresh short_id. Always 8 chars, alphanumeric, no separators."""
    return generate(ALPHABET, LENGTH)  # type: ignore[no-any-return]
