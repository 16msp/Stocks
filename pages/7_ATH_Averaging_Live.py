"""Streamlit page: live paper-trading tracker for the ATH-drop averaging strategy."""

import sys
from pathlib import Path

import streamlit as st

sys.path.append(str(Path(__file__).resolve().parent.parent))
from strategies import ath_averaging as ath  # noqa: E402
from strategies import nse_etf_momentum as base  # noqa: E402

st.set_page_config(page_title="ATH Averaging Live", page_icon="🔔", layout="wide")

st.title("🔔 ATH-Drop Averaging - Live Tracker")
st.caption(
    "Paper-trades the ATH-drop averaging rule in real time across a deduplicated, filtered NSE ETF "
    "universe (one ETF per underlying index - the highest-volume one; gold/silver and thin ETFs "
    "excluded). Click 'Fetch latest data' on the Weekly Momentum page first if today's data isn't "
    "in yet, then run the check below. Re-running never re-fetches data, never double-opens a "
    "tranche, and never averages down twice using the same trading day's price move. Positions "
    "already open in a symbol stay monitored even if it later drops out of the tracked universe."
)

status = base.get_db_status()
if not status.get("exists"):
    st.warning("No data stored yet. Go to the **NSE ETF Weekly Momentum** page and click **Fetch latest data** first.")
    st.stop()

search_query = st.text_input("🔍 Search (symbol or description) - filters every table on this page", key="ath_live_search")


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
c1, c2, c3 = st.columns(3)
c1.metric("ETFs tracked (raw)", status.get("known_etfs", 0))
c2.metric("After dedup", len(universe))
c3.metric("Final universe (monitored)", int(universe["included"].sum()) if not universe.empty else 0)

st.sidebar.header("Rule (fixed - see Backtest page for why)")
st.sidebar.write(f"Entry: -{ath.ATH_DROP_TRIGGER*100:.0f}% from ATH")
st.sidebar.write(f"Averaging: -{ath.AVG_DROP_TRIGGER*100:.0f}% from last buy, max {ath.MAX_TRANCHES} tranches")
st.sidebar.write(f"Exit: +{ath.SHORT_TERM_GAIN*100:.0f}% (<1yr) or {ath.LONG_TERM_CAGR*100:.0f}% CAGR (>=1yr)")

check_clicked = st.sidebar.button("🔔 Check signals now", type="primary", width="stretch")

if check_clicked:
    st.session_state["ath_live_result"] = ath.check_signals()

result = st.session_state.get("ath_live_result")

criteria_cols = {
    "symbol": "Symbol",
    "description": "Description",
    "tranche": "Tranche",
    "entry_date": "Entry date",
    "entry_price": st.column_config.NumberColumn("Entry price", format="%.2f"),
    "ath": st.column_config.NumberColumn("ATH", format="%.2f"),
    "pct_below_ath": st.column_config.NumberColumn("% below ATH", format="%.0f%%"),
}

if result is None:
    st.info("Click **Check signals now** in the sidebar to evaluate today's data across the ETF universe.")
else:
    if not result.ok:
        st.warning(result.message)
    else:
        st.success(result.message)
        if not result.newly_closed.empty:
            for _, row in result.newly_closed.iterrows():
                st.success(
                    f"🎯 **SELL ALERT** - {row['symbol']} ({row['description']}) tranche {row['tranche']} "
                    f"hit target (return {row['return_pct']:.0f}%, max drawdown while held {row['max_drawdown_pct']:.0f}%). "
                    f"Entered {row['entry_date']} at {row['entry_price']:.2f}."
                )

    st.subheader("✅ ETFs meeting entry criteria today")
    if result.ok and not result.meets_criteria.empty:
        st.dataframe(_filter(result.meets_criteria), column_config=criteria_cols, hide_index=True, width="stretch")
    else:
        st.write("(none today)")

    st.subheader(f"👀 Watchlist - already down {ath.WATCHLIST_MIN_DROP*100:.0f}%+ from ATH, not yet at the {ath.ATH_DROP_TRIGGER*100:.0f}% trigger")
    st.caption("Sorted closest-to-triggering first, so you can see what's approaching before it actually fires.")
    if result.ok and not result.watchlist.empty:
        st.dataframe(
            _filter(result.watchlist),
            column_config={
                "symbol": "Symbol", "description": "Description",
                "current_price": st.column_config.NumberColumn("Current price", format="%.2f"),
                "ath": st.column_config.NumberColumn("ATH", format="%.2f"),
                "pct_below_ath": st.column_config.NumberColumn("% below ATH", format="%.0f%%"),
                "pct_to_go": st.column_config.NumberColumn("% to go", format="%.0f%%"),
            },
            hide_index=True, width="stretch",
        )
    else:
        st.write("(none today)")

st.subheader("Open positions")
open_positions = ath.get_open_positions()
if check_clicked and result is not None and result.ok:
    open_positions = result.open_positions
if open_positions.empty:
    st.write("(no open positions)")
else:
    if "current_price" not in open_positions.columns:
        meta = base.read_meta().set_index("symbol")["category"].to_dict()
        open_positions = open_positions.assign(
            description=open_positions["symbol"].map(meta), current_price=None, unrealized_pct=None,
            current_drawdown_pct=(open_positions["min_low"] / open_positions["entry_price"] - 1) * 100,
            days_held=None,
        )
    st.dataframe(
        _filter(open_positions)[["symbol", "description", "tranche", "entry_date", "entry_price", "current_price", "unrealized_pct", "current_drawdown_pct", "days_held"]].sort_values("symbol"),
        column_config={
            "symbol": "Symbol",
            "description": "Description",
            "tranche": "Tranche",
            "entry_date": "Entry date",
            "entry_price": st.column_config.NumberColumn("Entry price", format="%.2f"),
            "current_price": st.column_config.NumberColumn("Current price", format="%.2f"),
            "unrealized_pct": st.column_config.NumberColumn("Unrealized %", format="%.0f%%"),
            "current_drawdown_pct": st.column_config.NumberColumn("Drawdown so far %", format="%.0f%%"),
            "days_held": "Days held",
        },
        hide_index=True, width="stretch",
    )

st.subheader("Trade history")
history = ath.get_trade_history()
if history.empty:
    st.write("(no closed trades yet)")
else:
    meta = base.read_meta().set_index("symbol")["category"].to_dict()
    history["description"] = history["symbol"].map(meta)
    wins = (history["status"] == "WIN").sum()
    st.caption(f"{len(history)} closed tranche(s) - {wins} win(s).")
    st.dataframe(
        _filter(history)[["symbol", "description", "tranche", "entry_date", "exit_date", "return_pct", "max_drawdown_pct"]],
        column_config={
            "symbol": "Symbol", "description": "Description", "tranche": "Tranche",
            "entry_date": "Entry date", "exit_date": "Exit date",
            "return_pct": st.column_config.NumberColumn("Return %", format="%.0f%%"),
            "max_drawdown_pct": st.column_config.NumberColumn("Max drawdown %", format="%.0f%%"),
        },
        hide_index=True, width="stretch",
    )
    st.download_button(
        "⬇️ Download trade history as CSV",
        data=history.to_csv(index=False).encode("utf-8"),
        file_name="ath_averaging_trade_history.csv",
        mime="text/csv",
    )
