"""Scenarios page: edit templates, toggle active."""
from __future__ import annotations

import asyncio

import streamlit as st

from admin.data import audit
from admin.data import scenarios as sc_data


def render(actor: str) -> None:
    st.header("🎬 Сценарии")
    st.caption(
        "Welcome / comment-to-DM / handover — это шаблоны автоответов. "
        "Можно изменить текст без перезапуска. "
        "`{first_name}`, `{tg_link}`, `{disclaimer}` — подставляются автоматически."
    )

    rows = asyncio.run(sc_data.list_all())
    if not rows:
        st.info("Сценариев пока нет.")
        return

    for row in rows:
        label = f"**{row['name']}** · {row['type']} · {'🟢 активен' if row['active'] else '⚪ выключен'}"
        with st.expander(label):  # noqa: SIM117
            with st.form(f"scenario_{row['id']}"):
                template = st.text_area(
                    "Шаблон",
                    value=row["template"] or "",
                    height=180,
                    help=(
                        "Доступные плейсхолдеры: {first_name} {tg_link} {disclaimer}\n"
                        "Smart-сценарий не использует шаблон — Claude генерирует ответ сам."
                    ),
                )
                active = st.checkbox("Активен", value=row["active"])

                col1, col2 = st.columns([1, 4])
                with col1:
                    save = st.form_submit_button("💾 Сохранить", use_container_width=True)
                with col2:
                    if row["metadata"]:
                        st.caption(f"metadata: `{dict(row['metadata'])}`")

                if save:
                    if template != (row["template"] or ""):
                        asyncio.run(sc_data.update_template(row["id"], template))
                        asyncio.run(audit.record_action(
                            actor=actor, action="scenario_update_template",
                            target_type="scenario", target_id=row["id"],
                            details={"new_length": len(template)},
                        ))
                    if active != row["active"]:
                        asyncio.run(sc_data.set_active(row["id"], active))
                        asyncio.run(audit.record_action(
                            actor=actor, action="scenario_set_active",
                            target_type="scenario", target_id=row["id"],
                            details={"active": active},
                        ))
                    st.success("Сохранено.")
                    st.rerun()
