"""Streamlit page: historical backtest of the sector reversal strategy."""

import sys
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st

sys.path.append(str(Path(__file__).resolve().parent.parent))
from strategies import nse_etf_momentum as base  # noqa: E402
from strategies import sector_reversal_backtest as bt  # noqa: E402
from strategies.sector_groups import SECTOR_GROUPS  # noqa: E402

st.set_page_config(page_title="Sector Reversal Backtest", page_icon="\U0001F9EA", layout="wide")

st.title("\U0001F9EA Sector Reversal Backtest")
st.caption(
    "Tests the reversal rule against years of real history: sector was down over the prior "
    "weeks, then turned positive -> buy, hold, and count it a win if the sector touches "
    "+20% at any point within the horizon (your app would alert you to sell that day). "
    "Not investment advice, and no backtest guarantees future results - use this to judge "
    "whether the rule has *any* historical edge before risking money, not as a promise."
)

all_symbols = sorted({s for syms in SECTOR_GROUPS.values() for s in syms})
depth = base.get_history_depth(all_symbols)
backtestable = 0
for syms in SECTOR_GROUPS.values():
    d = depth[depth["symbol"].isin(syms)]
    if not d.empty and d["years_of_history"].max() >= bt.MIN_YEARS_FOR_BACKTEST:
        backtestable += 1

c1, c2, c3 = st.columns(3)
c1.metric("Sectors with 2+ years history", f"{backtestable} / {len(SECTOR_GROUPS)}")
c2.metric("Oldest data point", str(depth["first_date"].min()) if not depth.empty else "-")
c3.metric("Deep-backfilled symbols", int((depth["days_stored"] > 0).sum()))

st.sidebar.header("Backfill")
st.sidebar.caption("One-time, idempotent - only pulls symbols/history not already stored. Needed before backtesting.")
if st.sidebar.button("\U0001F4E5 Run deep historical backfill", width="stretch"):
    log_box = st.status("Backfilling full available history for all sector ETFs...", expanded=True)
    result = base.backfill_history(all_symbols, progress=lambda m: log_box.write(m))
    log_box.update(label=result.message, state="complete", expanded=False)
    st.rerun()

st.sidebar.header("Backtest settings")
weeks = st.sidebar.slider("Weeks lookback for the signal", 2, 8, bt.DEFAULT_WEEKS)
horizon_days = st.sidebar.slider("Max holding horizon (days)", 90, 365, bt.DEFAULT_HORIZON_DAYS, step=15)
thresholds = st.sidebar.multiselect(
    "Entry thresholds to test (prior-weeks decline %)",
    options=[-3, -5, -8, -10, -12, -15, -20],
    default=bt.DEFAULT_THRESHOLDS,
)
run_clicked = st.sidebar.button("\U0001F9EA Run backtest", type="primary", width="stretch")

if run_clicked or "backtest_result" not in st.session_state:
    st.session_state["backtest_result"] = bt.run_backtest(thresholds=thresholds or bt.DEFAULT_THRESHOLDS, weeks=weeks, horizon_days=horizon_days)

result: bt.BacktestResult = st.session_state["backtest_result"]

if not result.ok:
    st.warning(result.message)
    if result.excluded_sectors:
        with st.expander("Sectors excluded (not enough history)"):
            for s, reason in result.excluded_sectors.items():
                st.write(f"- **{s}**: {reason}")
    st.stop()

st.success(result.message)

st.subheader("Win rate by entry threshold (pooled across all backtestable sectors)")
pooled = result.pooled_by_threshold.sort_values("threshold")
fig = px.bar(
    pooled, x="threshold", y="win_rate_pct", text="trades",
    labels={"threshold": "Entry threshold: prior weeks decline %", "win_rate_pct": "Win rate % (touched +20%)"},
)
fig.update_traces(texttemplate="%{text} trades", textposition="outside")
fig.update_layout(height=400, yaxis_range=[0, 100])
st.plotly_chart(fig, width="stretch")
st.dataframe(
    pooled,
    column_config={
        "threshold": "Entry threshold %",
        "trades": "Trades",
        "wins": "Wins",
        "win_rate_pct": st.column_config.NumberColumn("Win rate %", format="%.1f%%"),
        "avg_days_to_hit": st.column_config.NumberColumn("Avg days to hit target", format="%.0f"),
    },
    hide_index=True, width="stretch",
)
st.caption(
    "Trade counts are pooled across sectors and years - many are correlated (a market-wide rally "
    "lifts several sectors together), so treat this as directional evidence, not a precise probability."
)

with st.expander("Robustness check: early vs. late history (out-of-sample split)"):
    resolved = result.trades[result.trades["status"].isin(["WIN", "LOSS"])].copy()
    if not resolved.empty:
        resolved["entry_date"] = pd.to_datetime(resolved["entry_date"])
        cutoff = resolved["entry_date"].median()
        resolved["period"] = resolved["entry_date"].apply(lambda d: "early half" if d < cutoff else "late half")
        split = (
            resolved.groupby(["threshold", "period"])
            .agg(trades=("status", "count"), wins=("status", lambda s: (s == "WIN").sum()))
            .reset_index()
        )
        split["win_rate_pct"] = split["wins"] / split["trades"] * 100
        st.caption(f"Split at {cutoff.date()} (median entry date). A threshold whose win rate holds up in both halves is more trustworthy than one that only worked in one period.")
        st.dataframe(
            split.pivot(index="threshold", columns="period", values=["trades", "win_rate_pct"]),
            width="stretch",
        )

st.subheader("Per-sector breakdown")
per_sector = result.per_sector_threshold.sort_values(["threshold", "win_rate_pct"], ascending=[True, False])
per_sector["low_sample"] = per_sector["trades"] < 10
st.dataframe(
    per_sector,
    column_config={
        "sector": "Sector",
        "threshold": "Threshold %",
        "trades": "Trades",
        "wins": "Wins",
        "win_rate_pct": st.column_config.NumberColumn("Win rate %", format="%.1f%%"),
        "avg_days_to_hit": st.column_config.NumberColumn("Avg days to hit", format="%.0f"),
        "avg_loss_return_pct": st.column_config.NumberColumn("Avg return if missed", format="%.1f%%"),
        "low_sample": st.column_config.CheckboxColumn("Low sample (<10 trades)"),
    },
    hide_index=True, width="stretch",
)

if result.excluded_sectors:
    with st.expander(f"Sectors excluded from backtest ({len(result.excluded_sectors)}) - track these live instead"):
        for s, reason in result.excluded_sectors.items():
            st.write(f"- **{s}**: {reason}")

st.download_button(
    "⬇️ Download all trades as CSV",
    data=result.trades.to_csv(index=False).encode("utf-8"),
    file_name="sector_reversal_backtest_trades.csv",
    mime="text/csv",
)
