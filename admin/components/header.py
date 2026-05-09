"""Reusable header with logout button."""
from __future__ import annotations

import streamlit as st


def render(actor: str) -> None:
    cols = st.columns([4, 1])
    with cols[0]:
        st.caption(f"Вошла как: **{actor}**")
    with cols[1]:
        if st.button("Выйти", use_container_width=True):
            st.session_state.clear()
            st.rerun()
