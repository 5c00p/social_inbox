"""Localized labels for admin UI controls.

Kept Streamlit-independent so the mappings can be unit-tested without
spinning up a Streamlit runtime (streamlit lives in the optional `admin`
dependency group; the default test env doesn't install it).
"""

from __future__ import annotations

# Status values stay in English (they map straight to conversations.status in
# SQL), but the inbox status dropdown shows Russian labels via format_func.
# Order here is also the order in the selectbox.
STATUS_FILTER_OPTIONS: list[str] = [
    "Все",
    "active",
    "handover_pending",
    "handover_done",
    "closed",
]
STATUS_FILTER_LABELS_RU: dict[str, str] = {
    "Все": "Все",
    "active": "Активные",
    "handover_pending": "Ожидают оператора",
    "handover_done": "Переданы оператору",
    "closed": "Закрытые",
}
