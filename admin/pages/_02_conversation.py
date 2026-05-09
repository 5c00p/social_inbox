"""Conversation detail page: history + reply form + handover controls."""
from __future__ import annotations

import asyncio
import uuid

import streamlit as st

from admin.data import audit
from admin.data import conversations as conv_data
from admin.data import messages as msg_data
from app.models.events import OutgoingMessage
from app.providers import get_provider
from app.repos import messages as messages_repo


def render(actor: str) -> None:
    conv_id = st.session_state.get("selected_conversation_id")
    if not conv_id:
        st.info("Выбери диалог во вкладке «Входящие».")
        return

    conv = asyncio.run(conv_data.get_conversation(conv_id))
    if not conv:
        st.error("Диалог не найден.")
        return

    st.header(f"💬 Диалог с @{conv['username'] or '(нет username)'}")
    st.caption(
        f"{conv['full_name'] or '—'} · {conv['user_platform']} · "
        f"short_id: `{conv['short_id']}` · TG: {conv['tg_user_id'] or '—'}"
    )

    c1, c2, c3 = st.columns(3)
    with c1:
        st.metric("Статус", conv["status"])
    with c2:
        st.metric("AI режим", "вкл" if conv["smart_mode_enabled"] else "выкл")
    with c3:
        st.metric("Платформа", conv["user_platform"])

    if conv["handover_reason"]:
        st.warning(f"Причина handover: {conv['handover_reason']}")

    st.subheader("История сообщений")
    msgs = asyncio.run(msg_data.get_messages(conv_id, limit=100))
    for m in msgs:
        with st.chat_message("user" if m["direction"] == "in" else "assistant"):
            text = m["text"] or "_(пусто)_"
            if m["safety_blocked"]:
                text = f"🛑 _Заблокировано: {m['safety_reason']}_"
            st.write(text)
            cap = m["created_at"].strftime("%Y-%m-%d %H:%M")
            if m["claude_used"]:
                cap += f" · claude {m['claude_model']} ({m['claude_tokens_in']}+{m['claude_tokens_out']})"
            if m["scenario_id"]:
                cap += f" · scenario #{m['scenario_id']}"
            st.caption(cap)

    st.subheader("Ответ от Юли")
    with st.form("reply_form", clear_on_submit=True):
        reply_text = st.text_area("Текст", height=100, placeholder="Напиши сообщение пользователю...")
        submit = st.form_submit_button("Отправить")
    if submit and reply_text.strip():
        asyncio.run(_send_admin_reply(conv, reply_text.strip(), actor))
        st.success("Отправлено.")
        st.rerun()

    st.subheader("Управление")
    col1, col2, _ = st.columns(3)
    with col1:
        if conv["status"] == "handover_pending":
            if st.button("✅ Закрыть handover", use_container_width=True):
                asyncio.run(conv_data.close_handover(conv_id))
                asyncio.run(audit.record_action(
                    actor=actor, action="close_handover",
                    target_type="conversation", target_id=conv_id,
                ))
                st.rerun()
        else:
            if st.button("⚠️ Перевести в handover", use_container_width=True):
                asyncio.run(_manual_handover(conv, conv_id, actor))
                st.rerun()
    with col2:
        new_smart = st.toggle(
            "AI режим включён",
            value=conv["smart_mode_enabled"],
            help="Если выключить — Claude больше не будет отвечать этому пользователю.",
        )
        if new_smart != conv["smart_mode_enabled"]:
            asyncio.run(conv_data.set_smart_mode(conv["user_id"], new_smart))
            asyncio.run(audit.record_action(
                actor=actor, action="set_smart_mode",
                target_type="user", target_id=conv["user_id"],
                details={"enabled": new_smart},
            ))
            st.rerun()


async def _manual_handover(conv: object, conv_id: int, actor: str) -> None:
    from app.repos import conversations as convs_repo
    from app.repos import users as users_repo
    from app.services import handover as ho

    # conv is an asyncpg.Record — access like a dict
    short_id = conv["short_id"]  # type: ignore[index]
    user = await users_repo.get_by_short_id(short_id)
    if user is None:
        return
    conv_full = await convs_repo.get_active(user["id"], conv["user_platform"])  # type: ignore[index]
    if conv_full is None:
        from app.repos.pool import get_pool
        pool = await get_pool()
        conv_full = await pool.fetchrow("SELECT * FROM conversations WHERE id = $1", conv_id)
    if conv_full is None:
        return
    await ho.trigger_handover(
        conversation=conv_full, user=user,
        source="manual",
        reason=f"manual by {actor}",
    )
    await audit.record_action(
        actor=actor, action="trigger_handover",
        target_type="conversation", target_id=conv_id,
    )


async def _send_admin_reply(conv: object, text: str, actor: str) -> None:
    """Send a manual reply through the active provider and persist as outgoing message."""
    provider = get_provider()
    msg = OutgoingMessage(
        platform=conv["user_platform"],  # type: ignore[index]
        external_user_id=conv["external_id"],  # type: ignore[index]
        text=text,
        scenario_id=None,
    )
    external_id: str | None = None
    try:
        external_id = await provider.send(msg)
    except Exception as exc:
        st.error(f"Не удалось отправить через провайдер: {exc}")

    record_external_id = external_id if external_id else f"admin:{uuid.uuid4()}"
    await messages_repo.insert(
        conversation_id=conv["id"],  # type: ignore[index]
        direction="out",
        text=text,
        source="admin_reply",
        external_message_id=record_external_id,
    )
    await audit.record_action(
        actor=actor, action="reply",
        target_type="conversation", target_id=conv["id"],  # type: ignore[index]
        details={"length": len(text), "send_ok": external_id is not None},
    )
