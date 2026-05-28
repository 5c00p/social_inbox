"""Basic Auth for Streamlit admin dashboard.

Streamlit doesn't have native auth — we wrap each page entry with `require_auth()`.
Credentials checked against ADMIN_BASIC_AUTH_USER and ADMIN_BASIC_AUTH_PASSWORD env vars.

Why not OAuth/SSO: only 1-2 users (Yulia + Victor), single-machine deploy.
Upgrade path: when team grows, swap for streamlit-authenticator with hashed passwords.
"""

from __future__ import annotations

import secrets

import streamlit as st

from app.config import get_settings


def require_auth() -> str:
    """Display login form if not authenticated, else return logged-in username.

    Stops Streamlit execution on the login screen via `st.stop()`.
    """
    settings = get_settings()
    expected_user = settings.admin_basic_auth_user
    expected_pass = settings.admin_basic_auth_password

    if not expected_pass:
        st.error(
            "ADMIN_BASIC_AUTH_PASSWORD не настроен. " "Установи в .env и перезапусти контейнер."
        )
        st.stop()

    if st.session_state.get("auth_ok"):
        return st.session_state["auth_user"]  # type: ignore[no-any-return]

    st.title("🔐 Вход в админку")
    with st.form("login"):
        username = st.text_input("Пользователь")
        password = st.text_input("Пароль", type="password")
        submitted = st.form_submit_button("Войти")

    if submitted:
        # Encode to bytes before compare_digest: the string overload only
        # accepts ASCII, so a Cyrillic password (e.g. typed with the wrong
        # keyboard layout) would raise TypeError. UTF-8 bytes work for any
        # input while preserving constant-time semantics.
        if secrets.compare_digest(
            username.encode("utf-8"), expected_user.encode("utf-8")
        ) and secrets.compare_digest(password.encode("utf-8"), expected_pass.encode("utf-8")):
            st.session_state["auth_ok"] = True
            st.session_state["auth_user"] = username
            st.rerun()
        else:
            st.error("Неверный логин или пароль")

    st.stop()
