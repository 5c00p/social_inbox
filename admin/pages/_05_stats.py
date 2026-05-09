"""Stats page: simple charts and counters."""
from __future__ import annotations

import asyncio

import pandas as pd
import streamlit as st

from admin.data import stats as stats_data


def render(actor: str) -> None:
    st.header("📊 Статистика")

    conv = asyncio.run(stats_data.conversion_to_telegram(days=30))
    rate = (conv["converted"] / conv["total"] * 100) if conv["total"] else 0
    cols = st.columns(3)
    cols[0].metric("Лидов за 30 дней", conv["total"])
    cols[1].metric("Дошли до Telegram", conv["converted"])
    cols[2].metric("Conversion rate", f"{rate:.1f}%")

    st.subheader("Новые лиды по дням")
    daily = asyncio.run(stats_data.daily_new_leads(days=14))
    if daily:
        df = pd.DataFrame([{"day": r["day"], "count": r["count"]} for r in daily])
        df["day"] = pd.to_datetime(df["day"])
        st.line_chart(df.set_index("day"))
    else:
        st.info("Пока нет данных за период.")

    st.subheader("Эскалации по причинам (30 дней)")
    breakdown = asyncio.run(stats_data.handover_breakdown(days=30))
    if breakdown:
        df_b = pd.DataFrame([{"source": r["source"], "count": r["count"]} for r in breakdown])
        st.bar_chart(df_b.set_index("source"))
    else:
        st.info("Эскалаций пока не было.")

    st.subheader("Расход Claude (7 дней)")
    tokens = asyncio.run(stats_data.claude_token_usage(days=7))
    if tokens:
        df_t = pd.DataFrame([{
            "day": r["day"],
            "tokens_in": r["tokens_in"] or 0,
            "tokens_out": r["tokens_out"] or 0,
            "messages": r["messages_count"],
        } for r in tokens])
        df_t["day"] = pd.to_datetime(df_t["day"])
        st.dataframe(df_t.set_index("day"), use_container_width=True)
    else:
        st.info("Claude пока не использовался.")
