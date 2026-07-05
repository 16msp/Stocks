"""Streamlit page: live paper-trading tracker + sell alerts for the sector reversal strategy."""

import sys
from pathlib import Path

import streamlit as st

sys.path.append(str(Path(__file__).resolve().parent.parent))
from strategies import nse_etf_momentum as base  # noqa: E402
from strategies import sector_reversal_live as live  # noqa: E402
from strategies.sector_reversal_backtest import DEFAULT_HORIZON_DAYS, DEFAULT_WEEKS, TARGET_PCT  # noqa: E402

st.set_page_config(page_title="Live Alerts", page_icon="🔔", layout="wide")

st.title("🔔 Live Sector Reversal Tracker")
st.caption(
    "Paper-trades the reversal rule in real time, on every sector - including the ones too "
    "new to backtest. Click 'Fetch latest data' on the Weekly Momentum page first if today's "
    "data isn't in yet, then run the check below. Re-running never double-opens a position "
    "or re-fetches data - it only evaluates whatever is already stored."
)

status = base.get_db_status()
if not status.get("exists"):
    st.warning("No data stored yet. Go to the **NSE ETF Weekly Momentum** page and click **Fetch latest data** first.")
    st.stop()

st.sidebar.header("Entry rule")
entry_threshold = st.sidebar.number_input(
    "Entry threshold: prior weeks decline %", min_value=-50.0, max_value=0.0,
    value=live.DEFAULT_ENTRY_THRESHOLD, step=1.0,
    help="Only open a position if the sector fell more than this over the prior weeks before turning up. "
    "-10% is the backtest-informed default (see Backtest page) - it held up best out-of-sample.",
)
weeks = st.sidebar.slider("Weeks lookback for the signal", 2, 8, DEFAULT_WEEKS)
horizon_days = st.sidebar.slider("Max holding horizon (days)", 90, 365, DEFAULT_HORIZON_DAYS, step=15)
target_pct = st.sidebar.number_input("Target gain %", min_value=5.0, max_value=100.0, value=TARGET_PCT, step=1.0)

check_clicked = st.sidebar.button("🔔 Check signals now", type="primary", width="stretch")

if check_clicked:
    result = live.check_signals(entry_threshold=entry_threshold, weeks=weeks, horizon_days=horizon_days, target_pct=target_pct)
    st.session_state["live_result"] = result

result = st.session_state.get("live_result")

if result is None:
    st.info("Click **Check signals now** in the sidebar to evaluate today's data against your entry rule.")
else:
    if not result.ok:
        st.warning(result.message)
    else:
        st.success(result.message)

        if not result.newly_closed.empty:
            for _, row in result.newly_closed.iterrows():
                if row["exit_reason"] == "target_hit":
                    st.success(f"🎯 **SELL ALERT** - {row['sector']} touched +{target_pct:.0f}% (return {row['return_pct']:.1f}%). Entered {row['entry_date']}.")
                else:
                    st.warning(f"⏱️ **Time exit** - {row['sector']} didn't reach target within {horizon_days} days (return {row['return_pct']:.1f}%). Entered {row['entry_date']}.")

        if not result.newly_opened.empty:
            for _, row in result.newly_opened.iterrows():
                st.info(f"🆕 **New position** - {row['sector']} opened at index {row['entry_index']:.2f} (prior weeks {row['prior_weeks_change_pct']:.1f}%, latest week {row['latest_week_change_pct']:.1f}%).")

st.subheader("Open positions")
open_positions = live.get_open_positions()
if check_clicked and result is not None and result.ok:
    open_positions = result.open_positions
if open_positions.empty:
    st.write("(no open positions)")
else:
    if "current_index" not in open_positions.columns:
        open_positions = open_positions.assign(current_index=None, unrealized_pct=None, days_held=None)
    st.dataframe(
        open_positions[["sector", "entry_date", "entry_index", "current_index", "unrealized_pct", "days_held", "entry_threshold", "target_pct", "horizon_days"]],
        column_config={
            "sector": "Sector",
            "entry_date": "Entry date",
            "entry_index": st.column_config.NumberColumn("Entry index", format="%.2f"),
            "current_index": st.column_config.NumberColumn("Current index", format="%.2f"),
            "unrealized_pct": st.column_config.NumberColumn("Unrealized %", format="%.2f%%"),
            "days_held": "Days held",
            "entry_threshold": st.column_config.NumberColumn("Entry threshold %", format="%.1f%%"),
            "target_pct": st.column_config.NumberColumn("Target %", format="%.0f%%"),
            "horizon_days": "Horizon (days)",
        },
        hide_index=True, width="stretch",
    )

st.subheader("Trade history")
history = live.get_trade_history()
if history.empty:
    st.write("(no closed trades yet)")
else:
    wins = (history["status"] == "WIN").sum()
    st.caption(f"{len(history)} closed trade(s) - {wins} win(s), {len(history) - wins} time-exit loss(es).")
    st.dataframe(
        history[["sector", "entry_date", "exit_date", "exit_reason", "return_pct", "entry_threshold"]],
        column_config={
            "sector": "Sector",
            "entry_date": "Entry date",
            "exit_date": "Exit date",
            "exit_reason": "Exit reason",
            "return_pct": st.column_config.NumberColumn("Return %", format="%.2f%%"),
            "entry_threshold": st.column_config.NumberColumn("Entry threshold %", format="%.1f%%"),
        },
        hide_index=True, width="stretch",
    )
    st.download_button(
        "⬇️ Download trade history as CSV",
        data=history.to_csv(index=False).encode("utf-8"),
        file_name="sector_reversal_trade_history.csv",
        mime="text/csv",
    )
