"""Inbox page: list conversations, handover-pending first."""

from __future__ import annotations

import asyncio

import streamlit as st

from admin.data import conversations as conv_data
from admin.labels import STATUS_FILTER_LABELS_RU, STATUS_FILTER_OPTIONS
from admin.navigation import PENDING_PAGE_KEY


def render(actor: str) -> None:
    st.header("📥 Входящие диалоги")

    cols = st.columns([2, 2, 1])
    with cols[0]:
        status_filter = st.selectbox(
            "Статус",
            options=STATUS_FILTER_OPTIONS,
            format_func=lambda v: STATUS_FILTER_LABELS_RU.get(v, v),
            index=0,
        )
    with cols[1]:
        search = st.text_input("Поиск по username", placeholder="например: masha_p")
    with cols[2]:
        if st.button("🔄 Обновить", use_container_width=True):
            st.rerun()

    rows = asyncio.run(
        conv_data.list_conversations(
            status_filter=None if status_filter == "Все" else status_filter,
            search_username=search or None,
            limit=200,
        )
    )

    if not rows:
        st.info("Пока пусто.")
        return

    for row in rows:
        status_badge = _status_badge(row["status"])
        smart_badge = "" if row["smart_mode_enabled"] else " 🚫AI"
        title = (
            f"{status_badge} **@{row['username'] or '(нет)'}** "
            f"({row['full_name'] or '—'}) · {row['platform']}{smart_badge}"
        )

        with st.container(border=True):
            c1, c2 = st.columns([5, 1])
            with c1:
                st.markdown(title)
                if row["handover_reason"]:
                    st.caption(f"📌 {row['handover_reason']}")
                last = row["last_message_at"]
                st.caption(
                    f"Последнее: {last.strftime('%Y-%m-%d %H:%M') if last else '—'}"
                    f" · short_id: `{row['short_id']}`"
                )
            with c2:
                if st.button(
                    "Открыть",
                    key=f"open_{row['conversation_id']}",
                    use_container_width=True,
                ):
                    st.session_state["selected_conversation_id"] = row["conversation_id"]
                    # Streamlit forbids writing to the same key that backs an
                    # already-instantiated widget ('page_selector' is the sidebar
                    # radio's key). Use a sentinel key instead — streamlit_app.main
                    # pops it BEFORE the radio widget is created and uses it to
                    # pick the radio's initial index.
                    st.session_state[PENDING_PAGE_KEY] = "💬 Диалог"
                    st.rerun()


def _status_badge(status: str) -> str:
    return {
        "active": "🟢",
        "handover_pending": "🔴",
        "handover_done": "✅",
        "closed": "⚪",
    }.get(status, "❔")
