"""Streamlit page: per-ETF entry/exit strategy optimizer for ATH-drop averaging."""

import sys
from pathlib import Path

import streamlit as st

sys.path.append(str(Path(__file__).resolve().parent.parent))
from strategies import ath_averaging as ath  # noqa: E402
from strategies import ath_optimizer as opt  # noqa: E402
from strategies import nse_etf_momentum as base  # noqa: E402

st.set_page_config(page_title="ATH Strategy Optimizer", page_icon="🧪", layout="wide")

st.title("🧪 Per-ETF Strategy Optimizer")
st.caption(
    "The fixed ATH-Averaging rule (-20% entry, +25%/15% CAGR exit) is one-size-fits-all. This page "
    "instead sweeps entry depth (-15% to -35% from ATH) and exit targets (+15% to +30% short-term, "
    "12% to 20% CAGR long-term) independently *per ETF*, using its own history, and picks whichever "
    "combination produced the most total realized profit (or, optionally, the best annualized XIRR). "
    "Averaging step (-10%, max 3 tranches) is kept fixed. This is a research table, not a live "
    "tracker - it doesn't open real or paper positions. Not investment advice."
)

status = base.get_db_status()
if not status.get("exists"):
    st.warning("No data stored yet. Go to the **NSE ETF Weekly Momentum** page and click **Fetch latest data** first.")
    st.stop()

search_query = st.text_input("🔍 Search (symbol or description) - filters both tables below", key="ath_opt_search")


def _filter(df, cols=("symbol", "description")):
    if not search_query or df.empty:
        return df
    present = [c for c in cols if c in df.columns]
    if not present:
        return df
    mask = False
    for c in present:
        mask = mask | df[c].astype(str).str.contains(search_query, case=False, na=False)
    return df[mask]


universe = ath.get_universe()
c1, c2 = st.columns(2)
c1.metric("Final universe (monitored)", int(universe["included"].sum()) if not universe.empty else 0)
c2.metric("Data through", status.get("last_date") or "-")

st.sidebar.header("Optimizer settings")
objective_label = st.sidebar.radio(
    "Optimize each ETF for", ["Maximum total return (₹)", "Maximum annualized return (XIRR)"],
    help="Total return favors ETFs/combos with more frequent trades; XIRR favors faster capital turnover, "
    "even with fewer, smaller wins.",
)
objective = "total_gain" if objective_label.startswith("Maximum total") else "xirr_pct"
min_years = st.sidebar.slider("Min years of history required", 0.5, 3.0, 1.0, step=0.5)

run_clicked = st.sidebar.button("🧪 Run optimizer", type="primary", width="stretch")

if run_clicked or "ath_opt_result" not in st.session_state:
    with st.spinner("Sweeping entry/exit combinations across the universe..."):
        st.session_state["ath_opt_result"] = opt.run_optimizer(objective=objective, min_years=min_years)

result: opt.OptimizerResult = st.session_state["ath_opt_result"]

if not result.ok:
    st.warning(result.message)
    st.stop()

st.success(result.message)

display = result.universe.copy()
display["best_combo"] = display.apply(
    lambda r: f"-{r['entry_drop_pct']:.0f}% / +{r['short_term_gain_pct']:.0f}% (<1yr) / {r['long_term_cagr_pct']:.0f}% CAGR (>=1yr)",
    axis=1,
)
display["win_open"] = display.apply(lambda r: f"{int(r['wins'])} / {int(r['open_count'])}", axis=1)

st.subheader("Final universe (monitored) - best strategy per ETF")
show_cols = ["symbol", "description", "best_combo", "cmp", "next_entry_price", "pct_wait", "win_rate_pct", "win_open", "total_gain", "xirr_pct"]
st.dataframe(
    _filter(display)[show_cols],
    column_config={
        "symbol": "Symbol",
        "description": "Description",
        "best_combo": "Strategy - best combination",
        "cmp": st.column_config.NumberColumn("CMP", format="%.2f"),
        "next_entry_price": st.column_config.NumberColumn("Next entry price", format="%.2f"),
        "pct_wait": st.column_config.NumberColumn("% wait to take position", format="%.0f%%"),
        "win_rate_pct": st.column_config.NumberColumn("Win rate", format="%.0f%%"),
        "win_open": "Win / Open count",
        "total_gain": st.column_config.NumberColumn("Total gain ₹", format="₹%.0f"),
        "xirr_pct": st.column_config.NumberColumn("XIRR", format="%.0f%%"),
    },
    hide_index=True, width="stretch",
)
st.caption(
    "'% wait to take position' = how much further CMP needs to fall to reach the next entry/averaging "
    "price. A blank next-entry-price means that ETF's cycle is already fully loaded (3 tranches bought) "
    "under its own best combination."
)

st.subheader("Historical trades (using each ETF's own best combination)")
trades_display = result.trades.copy()
trade_cols = ["symbol", "description", "tranche", "entry_date", "entry_price", "status", "exit_date", "days_held", "return_pct", "gain_rupees"]
st.dataframe(
    _filter(trades_display)[trade_cols],
    column_config={
        "symbol": "Symbol", "description": "Description", "tranche": "Tranche",
        "entry_date": "Entry date", "entry_price": st.column_config.NumberColumn("Entry price", format="%.2f"),
        "status": "Status", "exit_date": "Exit date", "days_held": "Days held",
        "return_pct": st.column_config.NumberColumn("Return %", format="%.0f%%"),
        "gain_rupees": st.column_config.NumberColumn("Gain ₹", format="₹%.0f"),
    },
    hide_index=True, width="stretch",
)

st.download_button(
    "⬇️ Download per-ETF strategy table as CSV",
    data=display[show_cols].to_csv(index=False).encode("utf-8"),
    file_name="ath_strategy_optimizer_universe.csv",
    mime="text/csv",
)
st.download_button(
    "⬇️ Download historical trades as CSV",
    data=trades_display[trade_cols].to_csv(index=False).encode("utf-8"),
    file_name="ath_strategy_optimizer_trades.csv",
    mime="text/csv",
)
