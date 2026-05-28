"""Test the page-switch helper used by Streamlit admin.

Original bug (error02.md):

    StreamlitAPIException: st.session_state.page_selector cannot be modified
    after the widget with key page_selector is instantiated.

Root cause: admin/pages/_01_inbox.py wrote to st.session_state['page_selector']
directly from the 'Открыть' button handler, but the sidebar radio in
admin/streamlit_app.main() was already created with key='page_selector' in
the same rerun. Streamlit refuses post-widget writes.

Fix: pages write to a SENTINEL key (PENDING_PAGE_KEY), and main() pops it
BEFORE building the radio. The helper below picks the radio's initial index
from the sentinel.
"""

from __future__ import annotations

from admin.navigation import PENDING_PAGE_KEY, resolve_initial_page_index

PAGES = [
    "📥 Входящие",
    "💬 Диалог",
    "🎬 Сценарии",
    "🔑 Ключевые слова",
    "📊 Статистика",
]


def test_pending_page_key_constant_is_not_a_widget_key() -> None:
    """Sentinel MUST be distinct from the radio's widget key 'page_selector'.

    If they ever collide, the original bug returns.
    """
    assert PENDING_PAGE_KEY != "page_selector"
    assert PENDING_PAGE_KEY.startswith("_")  # signals 'internal'


def test_resolve_index_none_falls_back_to_first_page() -> None:
    assert resolve_initial_page_index(PAGES, None) == 0


def test_resolve_index_unknown_page_falls_back_to_first_page() -> None:
    assert resolve_initial_page_index(PAGES, "🪐 Unknown Page") == 0


def test_resolve_index_returns_dialog_when_inbox_requests_it() -> None:
    """The exact scenario from error02.md: inbox 'Открыть' wants 💬 Диалог."""
    assert resolve_initial_page_index(PAGES, "💬 Диалог") == 1


def test_resolve_index_handles_every_page() -> None:
    for idx, page in enumerate(PAGES):
        assert resolve_initial_page_index(PAGES, page) == idx


def test_session_state_pop_pattern_consumes_sentinel() -> None:
    """Document the contract main() relies on: dict.pop returns the value and
    removes it. The next rerun must NOT see the same pending request again
    (otherwise the radio is permanently stuck on Диалог).
    """
    fake_session: dict[str, str] = {PENDING_PAGE_KEY: "💬 Диалог"}
    pending = fake_session.pop(PENDING_PAGE_KEY, None)
    assert pending == "💬 Диалог"
    assert PENDING_PAGE_KEY not in fake_session
