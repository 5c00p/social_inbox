"""Admin dashboard for social_inbox.

Run:
    streamlit run admin/streamlit_app.py --server.port 8501

Pages are dispatched manually from a sidebar radio (instead of Streamlit's
auto-discovery from pages/ directory) so we can hide unauthorized pages.
"""
from __future__ import annotations

import streamlit as st

from admin.auth import require_auth
from admin.components import header
from admin.pages import _01_inbox, _02_conversation, _03_scenarios, _04_keywords, _05_stats

st.set_page_config(
    page_title="social_inbox · admin",
    page_icon="💚",
    layout="wide",
)

PAGES = {
    "📥 Входящие": _01_inbox.render,
    "💬 Диалог": _02_conversation.render,
    "🎬 Сценарии": _03_scenarios.render,
    "🔑 Ключевые слова": _04_keywords.render,
    "📊 Статистика": _05_stats.render,
}


def main() -> None:
    actor = require_auth()

    with st.sidebar:
        st.title("social_inbox")
        page_label = st.radio(
            "Раздел",
            list(PAGES.keys()),
            label_visibility="collapsed",
            key="page_selector",
        )

    header.render(actor)
    PAGES[page_label](actor=actor)  # type: ignore[index]


if __name__ == "__main__":
    main()
