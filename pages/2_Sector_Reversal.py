"""Streamlit page: NSE Sector ETF Reversal screen."""

import sys
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st

sys.path.append(str(Path(__file__).resolve().parent.parent))
from strategies import nse_etf_momentum as base  # noqa: E402
from strategies import sector_reversal as strategy  # noqa: E402
from strategies.sector_groups import SECTOR_GROUPS  # noqa: E402

st.set_page_config(page_title="Sector ETF Reversal", page_icon="🔄", layout="wide")

st.title("🔄 Sector ETF Reversal Screen")
st.caption(
    "Combines same-sector ETFs from different AMCs into one equal-weighted sector index "
    "(e.g. all 20 Banking ETFs -> one Banking line). Flags sectors that were falling over "
    "the prior weeks and just turned positive in the latest week - a classic 'down, now "
    "reversing' setup. Uses the same locally stored data as the Weekly Momentum page - no "
    "separate fetch needed. Not investment advice."
)

status = base.get_db_status()
if not status.get("exists"):
    st.warning("No data stored yet. Go to the **NSE ETF Weekly Momentum** page and click **Fetch latest data** first.")
    st.stop()

c1, c2 = st.columns(2)
c1.metric("Sectors tracked", len(SECTOR_GROUPS))
c2.metric("Data through", status.get("last_date") or "-")

st.sidebar.header("Settings")
weeks = st.sidebar.slider("Weeks to analyze", 3, 8, 4, help="Needs weeks+1 weekly data points; falls back to whatever is stored if less.")
min_avg_volume = st.sidebar.number_input("Min avg weekly sector volume (shares)", min_value=0, value=0, step=10000)
run_clicked = st.sidebar.button("🔄 Run sector analysis", type="primary", width="stretch")

if run_clicked or "sector_result" not in st.session_state:
    st.session_state["sector_result"] = strategy.analyze(weeks=weeks, min_avg_volume=min_avg_volume)

result: strategy.SectorReversalResult = st.session_state["sector_result"]

if not result.ok:
    st.warning(result.message)
    st.stop()

st.success(result.message)

reversing = result.summary[result.summary["reversing"]]
st.subheader(f"🟢 Reversing sectors ({len(reversing)})")
st.caption("Net down over the prior weeks, turned positive in the latest week.")

pct_cols = {
    "sector": "Sector",
    "etfs_used": st.column_config.NumberColumn("ETFs"),
    "prior_weeks_change_pct": st.column_config.NumberColumn("Prior weeks %", format="%.2f%%"),
    "latest_week_change_pct": st.column_config.NumberColumn("Latest week %", format="%.2f%%"),
    "latest_week_volume_change_pct": st.column_config.NumberColumn("Latest vol %", format="%.2f%%"),
    "avg_weekly_volume": st.column_config.NumberColumn("Avg weekly vol", format="%,d"),
}
show_cols = ["sector", "etfs_used", "prior_weeks_change_pct", "latest_week_change_pct", "latest_week_volume_change_pct", "avg_weekly_volume"]

if reversing.empty:
    st.write("(none right now)")
else:
    st.dataframe(reversing[show_cols], column_config=pct_cols, hide_index=True, width="stretch")

st.subheader("All sectors")
st.dataframe(result.summary[show_cols], column_config=pct_cols, hide_index=True, width="stretch")

st.subheader("Sector trend lines")
default_sectors = reversing["sector"].tolist() or result.summary["sector"].head(5).tolist()
chosen = st.multiselect("Sectors to chart", options=result.summary["sector"].tolist(), default=default_sectors)
if chosen:
    chart_df = result.chart_long[result.chart_long["sector"].isin(chosen)]
    fig = px.line(
        chart_df, x="week_key", y="rebased_index", color="sector", markers=True,
        labels={"week_key": "Week", "rebased_index": "Index (rebased to 100 at window start)"},
    )
    fig.add_hline(y=100, line_dash="dot", line_color="gray")
    fig.update_layout(height=450, legend_title_text="Sector")
    st.plotly_chart(fig, width="stretch")

with st.expander("Sector -> ETF composition"):
    for sector, symbols in SECTOR_GROUPS.items():
        st.write(f"**{sector}** ({len(symbols)}): {', '.join(symbols)}")

if result.missing_symbols:
    with st.expander(f"⚠️ Symbols missing from local data ({sum(len(v) for v in result.missing_symbols.values())})"):
        for sector, syms in result.missing_symbols.items():
            st.write(f"**{sector}**: {', '.join(syms)}")

st.download_button(
    "⬇️ Download sector summary as CSV",
    data=result.summary.to_csv(index=False).encode("utf-8"),
    file_name="nse_sector_reversal.csv",
    mime="text/csv",
)
