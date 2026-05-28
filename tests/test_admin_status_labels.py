"""Test the Russian-label mapping for the inbox status selectbox.

Background (task_add_01.md): the dropdown originally showed raw English
technical values ('active', 'handover_pending', etc.). Yulia asked for
Russian labels. The fix keeps values English (they're used directly in the
SQL filter `c.status = $1`) and displays Russian labels via Streamlit's
`format_func` parameter.

These tests guard:
- Every option in the dropdown has a Russian label
- 'Все' is still a sentinel that means 'no filter' (used by render() to
  pass status_filter=None to the data layer)
- The English values match the CHECK constraint on conversations.status
"""

from __future__ import annotations

from admin.labels import STATUS_FILTER_LABELS_RU, STATUS_FILTER_OPTIONS

# Mirror of CHECK constraint on conversations.status (see migrations/001).
DB_STATUSES = {"active", "closed", "handover_pending", "handover_done"}


def test_every_option_has_a_russian_label() -> None:
    for option in STATUS_FILTER_OPTIONS:
        assert option in STATUS_FILTER_LABELS_RU, f"missing label for {option!r}"


def test_all_filter_values_are_either_db_statuses_or_the_sentinel() -> None:
    """Each option must be a valid DB status (forwarded to SQL) or 'Все' (sentinel)."""
    for option in STATUS_FILTER_OPTIONS:
        assert (
            option == "Все" or option in DB_STATUSES
        ), f"{option!r} is neither 'Все' nor a known conversations.status value"


def test_first_option_is_the_no_filter_sentinel() -> None:
    """render() compares the picked value against 'Все' to decide whether to pass
    status_filter=None to the data layer. The first option must be that sentinel
    so the page opens with 'all conversations' by default."""
    assert STATUS_FILTER_OPTIONS[0] == "Все"


def test_all_labels_are_cyrillic_or_the_word_vse() -> None:
    """Sanity: every displayed label is Russian, not the raw English value."""
    for option, label in STATUS_FILTER_LABELS_RU.items():
        assert label != "", f"empty label for {option!r}"
        if option == "Все":
            continue
        # At least one Cyrillic letter in the label.
        assert any(
            "а" <= ch.lower() <= "я" or ch == "ё" for ch in label
        ), f"{label!r} is not Russian"


def test_specific_translations() -> None:
    assert STATUS_FILTER_LABELS_RU["active"] == "Активные"
    assert STATUS_FILTER_LABELS_RU["handover_pending"] == "Ожидают оператора"
    assert STATUS_FILTER_LABELS_RU["handover_done"] == "Переданы оператору"
    assert STATUS_FILTER_LABELS_RU["closed"] == "Закрытые"
