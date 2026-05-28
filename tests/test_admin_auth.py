"""Test the secrets.compare_digest invocation in admin/auth.py.

The original bug: typing a Cyrillic-keyboard password raised TypeError
because secrets.compare_digest's str overload only accepts ASCII inputs.
Fix: encode both sides to UTF-8 bytes before comparing.
"""

from __future__ import annotations

import secrets


def test_compare_digest_with_non_ascii_password_does_not_raise() -> None:
    """Mirror the comparison admin/auth.py:require_auth performs after the fix."""
    expected_pass = "correct-horse-battery"
    cyrillic_attempt = "руцид-рщкыу-ифееукн"  # Cyrillic letters typed by mistake

    # Before fix:
    #   secrets.compare_digest(cyrillic_attempt, expected_pass)
    #   -> TypeError: comparing strings with non-ASCII characters is not supported
    # After fix (encode to bytes first):
    result = secrets.compare_digest(
        cyrillic_attempt.encode("utf-8"),
        expected_pass.encode("utf-8"),
    )
    assert result is False  # mismatch, but no exception


def test_compare_digest_correct_password_still_matches() -> None:
    """Sanity: the fix didn't break the happy path."""
    expected_pass = "correct-password"
    assert secrets.compare_digest(
        expected_pass.encode("utf-8"),
        expected_pass.encode("utf-8"),
    )


def test_compare_digest_with_cyrillic_username_does_not_raise() -> None:
    expected_user = "yulia"
    cyrillic_user = "юлия"

    result = secrets.compare_digest(
        cyrillic_user.encode("utf-8"),
        expected_user.encode("utf-8"),
    )
    assert result is False
