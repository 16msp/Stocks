"""Streamlit page: backtest for the ATH-drop averaging strategy."""

import sys
from pathlib import Path

import streamlit as st

sys.path.append(str(Path(__file__).resolve().parent.parent))
from strategies import ath_averaging as ath  # noqa: E402
from strategies import nse_etf_momentum as base  # noqa: E402

st.set_page_config(page_title="ATH Averaging Backtest", page_icon="\U0001F4C9", layout="wide")

st.title("\U0001F4C9 ATH-Drop Averaging Backtest")
st.caption(
    f"Rule: buy tranche 1 when price first touches -{ath.ATH_DROP_TRIGGER*100:.0f}% below its running "
    f"all-time high; average with tranche 2/3 on a further -{ath.AVG_DROP_TRIGGER*100:.0f}% touch from the "
    f"last buy (max {ath.MAX_TRANCHES} tranches). Sell each tranche independently at "
    f"+{ath.SHORT_TERM_GAIN*100:.0f}% if held under 1 year, or once it clears {ath.LONG_TERM_CAGR*100:.0f}% "
    "CAGR since entry if held longer. Trades real, individual ETFs (not a synthetic composite). "
    "Universe is deduplicated (one ETF per underlying index - the highest-volume one), gold/silver "
    f"ETFs are excluded, and anything averaging under {ath.MIN_AVG_VOLUME:,} shares/day is dropped as "
    "too thin to matter. Not investment advice."
)

status = base.get_db_status()
if not status.get("exists"):
    st.warning("No data stored yet. Go to the **NSE ETF Weekly Momentum** page and click **Fetch latest data** first.")
    st.stop()

search_query = st.text_input("🔍 Search (symbol or description) - filters every table on this page", key="ath_backtest_search")


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
c1, c2, c3, c4 = st.columns(4)
c1.metric("ETFs tracked (raw)", status.get("known_etfs", 0))
c2.metric("After dedup", len(universe))
c3.metric("Final universe (traded)", int(universe["included"].sum()) if not universe.empty else 0)
c4.metric("Data through", status.get("last_date") or "-")

st.sidebar.header("Backfill")
st.sidebar.caption("One-time, idempotent - needed for enough history to backtest. Already run for the full ETF universe.")
if st.sidebar.button("\U0001F4E5 Deep-backfill all ETFs", width="stretch"):
    meta = base.read_meta()
    log_box = st.status("Backfilling full available history for all NSE ETFs...", expanded=True)
    result = base.backfill_history(meta["symbol"].tolist(), progress=lambda m: log_box.write(m))
    log_box.update(label=result.message, state="complete", expanded=False)
    st.rerun()

run_clicked = st.sidebar.button("\U0001F4C9 Run backtest", type="primary", width="stretch")

if run_clicked or "ath_backtest_result" not in st.session_state:
    st.session_state["ath_backtest_result"] = ath.run_backtest()

result: ath.AthBacktestResult = st.session_state["ath_backtest_result"]

if not result.ok:
    st.warning(result.message)
    st.stop()

st.success(result.message)

resolved = result.trades[result.trades["status"] == "WIN"]
cagr_display = f"{result.overall_cagr_pct:.0f}%" if result.overall_cagr_pct is not None else "n/a"
c1, c2, c3 = st.columns(3)
c1.metric("Overall CAGR (XIRR)", cagr_display, help="Solved across every tranche's cash flow (entry outflow, exit/mark-to-market inflow) - the correct way to annualize a strategy with many overlapping trades of different durations.")
c2.metric("Total realized gain", f"₹{result.total_realized_gain:,.0f}")
c3.metric("Total capital deployed", f"₹{result.total_deployed:,.0f}")

c4, c5, c6 = st.columns(3)
c4.metric("Resolved trades", len(resolved))
c5.metric("Symbols traded", result.trades["symbol"].nunique())
c6.metric("Avg days held (wins)", f"{resolved['days_held'].mean():.0f}")

st.subheader("Per-symbol results")
per_symbol_cols = {
    "symbol": "Symbol",
    "description": "Description",
    "tranches": "Tranches",
    "wins": "Wins",
    "pending": "Pending",
    "win_rate_pct": st.column_config.NumberColumn("Win rate %", format="%.0f%%"),
    "realized_gain": st.column_config.NumberColumn("Realized gain ₹", format="₹%.0f"),
    "avg_days_held": st.column_config.NumberColumn("Avg days held", format="%.0f"),
    "worst_drawdown_pct": st.column_config.NumberColumn("Worst drawdown %", format="%.0f%%"),
}
st.dataframe(
    _filter(result.per_symbol).sort_values("realized_gain", ascending=False),
    column_config=per_symbol_cols, hide_index=True, width="stretch",
)

trade_display_cols = {
    "symbol": "Symbol",
    "description": "Description",
    "tranche": "Tranche",
    "entry_date": "Entry date",
    "entry_price": st.column_config.NumberColumn("Entry price", format="%.2f"),
    "status": "Status",
    "exit_date": "Exit date",
    "days_held": "Days held",
    "return_pct": st.column_config.NumberColumn("Return %", format="%.0f%%"),
    "max_drawdown_pct": st.column_config.NumberColumn("Max drawdown %", format="%.0f%%"),
    "gain_rupees": st.column_config.NumberColumn("Gain ₹", format="₹%.0f"),
}
with st.expander(f"All trades ({len(result.trades)})"):
    st.dataframe(_filter(result.trades), column_config=trade_display_cols, hide_index=True, width="stretch")

if result.excluded:
    with st.expander(f"Symbols excluded - not enough history ({len(result.excluded)})"):
        st.write(", ".join(result.excluded.keys()))

with st.expander("ETF universe - deduplication & filtering detail"):
    st.caption(
        f"{universe['group_key'].nunique() if not universe.empty else 0} unique underlying-index groups. "
        f"{int(universe['excluded_commodity'].sum()) if not universe.empty else 0} gold/silver ETFs excluded, "
        f"{int(universe['excluded_low_volume'].sum()) if not universe.empty else 0} excluded for volume "
        f"under {ath.MIN_AVG_VOLUME:,}/day."
    )
    st.dataframe(
        _filter(universe, cols=("symbol", "category")).sort_values(["included", "avg_volume"], ascending=[False, False]),
        column_config={
            "symbol": "Symbol", "category": "Category", "group_key": "Group",
            "avg_volume": st.column_config.NumberColumn("Avg volume (90d)", format="%d"),
            "is_representative": st.column_config.CheckboxColumn("Highest-vol in group"),
            "excluded_commodity": st.column_config.CheckboxColumn("Gold/Silver"),
            "excluded_low_volume": st.column_config.CheckboxColumn("Too thin"),
            "included": st.column_config.CheckboxColumn("Traded"),
        },
        hide_index=True, width="stretch",
    )

st.download_button(
    "⬇️ Download all trades as CSV",
    data=result.trades.to_csv(index=False).encode("utf-8"),
    file_name="ath_averaging_backtest_trades.csv",
    mime="text/csv",
)
