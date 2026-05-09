"""Keywords page: list + create + edit + delete."""
from __future__ import annotations

import asyncio

import streamlit as st

from admin.data import audit
from admin.data import keywords as kw_data
from admin.data import scenarios as sc_data


def render(actor: str) -> None:
    st.header("🔑 Ключевые слова")
    st.caption(
        "Когда подписчик пишет ключевое слово в комментарии или DM — "
        "запускается соответствующий сценарий. Приоритет: меньше = выше."
    )

    scenarios = asyncio.run(sc_data.list_all())
    scenario_options = {s["id"]: f"{s['name']} ({s['type']})" for s in scenarios}

    with st.expander("➕ Добавить keyword"):  # noqa: SIM117
        with st.form("new_keyword"):
            cols = st.columns([2, 1, 1])
            with cols[0]:
                kw = st.text_input("Слово/фраза", placeholder="например: МАСЛА")
            with cols[1]:
                match_type = st.selectbox("Тип", ["contains", "exact", "regex"])
            with cols[2]:
                context = st.selectbox("Где", ["dm", "comment", "both"])

            cols2 = st.columns([2, 1, 1])
            with cols2[0]:
                scenario_id = st.selectbox(
                    "Сценарий",
                    options=list(scenario_options.keys()),
                    format_func=lambda i: scenario_options[i],
                )
            with cols2[1]:
                priority = st.number_input("Приоритет", value=100, min_value=1, max_value=999)
            with cols2[2]:
                case_sensitive = st.checkbox("Учитывать регистр", value=False)

            if st.form_submit_button("Создать"):
                if not kw.strip():
                    st.error("Слово не может быть пустым.")
                else:
                    new_id = asyncio.run(kw_data.create(
                        keyword=kw.strip(),
                        match_type=match_type,
                        context=context,
                        scenario_id=scenario_id,
                        priority=priority,
                        case_sensitive=case_sensitive,
                    ))
                    asyncio.run(audit.record_action(
                        actor=actor, action="keyword_create",
                        target_type="keyword", target_id=new_id,
                        details={"keyword": kw.strip(), "scenario_id": scenario_id},
                    ))
                    st.success(f"Создан keyword #{new_id}.")
                    st.rerun()

    rows = asyncio.run(kw_data.list_all())
    for row in rows:
        label = (
            f"**{row['keyword']}** ({row['match_type']}/{row['context']}) → "
            f"{row['scenario_name']} · prio {row['priority']} · "
            f"{'🟢' if row['active'] else '⚪'}"
        )
        with st.expander(label):  # noqa: SIM117
            with st.form(f"kw_{row['id']}"):
                cols = st.columns([2, 1, 1])
                with cols[0]:
                    new_kw = st.text_input("Слово/фраза", value=row["keyword"])
                with cols[1]:
                    new_mt = st.selectbox(
                        "Тип", ["contains", "exact", "regex"],
                        index=["contains", "exact", "regex"].index(row["match_type"]),
                    )
                with cols[2]:
                    new_ctx = st.selectbox(
                        "Где", ["dm", "comment", "both"],
                        index=["dm", "comment", "both"].index(row["context"]),
                    )

                cols2 = st.columns([2, 1, 1])
                with cols2[0]:
                    sc_keys = list(scenario_options.keys())
                    new_sid = st.selectbox(
                        "Сценарий",
                        options=sc_keys,
                        index=sc_keys.index(row["scenario_id"]) if row["scenario_id"] in sc_keys else 0,
                        format_func=lambda i: scenario_options[i],
                    )
                with cols2[1]:
                    new_prio = st.number_input("Приоритет", value=row["priority"], min_value=1, max_value=999)
                with cols2[2]:
                    new_cs = st.checkbox("Учитывать регистр", value=row["case_sensitive"])

                new_active = st.checkbox("Активен", value=row["active"])

                bcols = st.columns([1, 1, 4])
                with bcols[0]:
                    save = st.form_submit_button("💾 Сохранить")
                with bcols[1]:
                    delete = st.form_submit_button("🗑 Удалить", type="secondary")

                if save:
                    asyncio.run(kw_data.update(
                        row["id"],
                        keyword=new_kw, match_type=new_mt, context=new_ctx,
                        scenario_id=new_sid, priority=new_prio,
                        case_sensitive=new_cs, active=new_active,
                    ))
                    asyncio.run(audit.record_action(
                        actor=actor, action="keyword_update",
                        target_type="keyword", target_id=row["id"],
                    ))
                    st.success("Сохранено.")
                    st.rerun()
                if delete:
                    asyncio.run(kw_data.delete(row["id"]))
                    asyncio.run(audit.record_action(
                        actor=actor, action="keyword_delete",
                        target_type="keyword", target_id=row["id"],
                        details={"keyword": row["keyword"]},
                    ))
                    st.success("Удалено.")
                    st.rerun()
