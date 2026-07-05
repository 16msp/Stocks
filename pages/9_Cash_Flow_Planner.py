"""Streamlit page: capital/cash-flow planning for the ATH strategy optimizer's trades."""

import sys
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

sys.path.append(str(Path(__file__).resolve().parent.parent))
from strategies import ath_optimizer as opt  # noqa: E402
from strategies import nse_etf_momentum as base  # noqa: E402

st.set_page_config(page_title="Cash Flow Planner", page_icon="💰", layout="wide")

st.title("💰 Cash Flow Planner")
st.caption(
    "Every backtested trade ties up ₹50,000 from entry until it resolves. Run enough ETFs at once and "
    "their holding periods overlap - this page shows how many positions would be open simultaneously, "
    "how much capital that requires, and - since you're funding this from salary, not unlimited capital - "
    "lets you test whether a real starting amount + monthly top-up could have kept up, or whether you'd "
    "have had to skip signals for lack of cash. Not investment advice."
)

status = base.get_db_status()
if not status.get("exists"):
    st.warning("No data stored yet. Go to the **NSE ETF Weekly Momentum** page and click **Fetch latest data** first.")
    st.stop()

st.sidebar.header("Optimizer settings")
objective_label = st.sidebar.radio(
    "Each ETF's best combo optimizes for", ["Maximum total return (₹)", "Maximum annualized return (XIRR)"],
)
objective = "total_gain" if objective_label.startswith("Maximum total") else "xirr_pct"
run_clicked = st.sidebar.button("💰 Run cash-flow analysis", type="primary", width="stretch")

if run_clicked or "cashflow_opt_result" not in st.session_state:
    with st.spinner("Optimizing per-ETF strategies and analyzing overlap..."):
        opt_result = opt.run_optimizer(objective=objective)
        st.session_state["cashflow_opt_result"] = opt_result
        st.session_state["cashflow_cf_result"] = opt.analyze_cashflow(opt_result.trades) if opt_result.ok else None

opt_result: opt.OptimizerResult = st.session_state.get("cashflow_opt_result")
cf: opt.CashFlowResult = st.session_state.get("cashflow_cf_result")

if opt_result is None:
    st.info("Click **Run cash-flow analysis** in the sidebar to get started.")
    st.stop()
if not opt_result.ok or cf is None or not cf.ok:
    st.warning((opt_result.message if not opt_result.ok else cf.message) if cf else opt_result.message)
    st.stop()

st.success(cf.message)

c1, c2, c3, c4 = st.columns(4)
c1.metric("Peak concurrent tranches", cf.peak_concurrent, help=f"On {cf.peak_date.date()}")
c2.metric("Peak capital required", f"₹{cf.peak_capital:,.0f}")
c3.metric("Average concurrent tranches", f"{cf.avg_concurrent:.1f}")
c4.metric("Capital needed for zero missed trades", f"₹{abs(cf.min_cumulative_balance):,.0f}", help="If starting from ₹0 and only rotating exit proceeds into new entries.")

st.subheader("How many positions overlap over time")
fig1 = go.Figure()
fig1.add_trace(go.Scatter(x=cf.daily["date"], y=cf.daily["open_count"], mode="lines", fill="tozeroy", name="Concurrent open tranches"))
fig1.update_layout(height=350, yaxis_title="Concurrent open tranches", xaxis_title="Date", showlegend=False)
st.plotly_chart(fig1, width="stretch")
st.caption("Sharp spikes mean a market-wide selloff triggered many ETFs' entry signals at once - exactly when capital demand is highest.")

st.subheader("Monthly capital needed (new entries) vs. capital freed (exits)")
monthly = cf.monthly.copy()
fig2 = go.Figure()
fig2.add_trace(go.Bar(x=monthly["month"], y=monthly["capital_needed"], name="Capital needed (entries)", marker_color="#D85A30"))
fig2.add_trace(go.Bar(x=monthly["month"], y=monthly["capital_freed"], name="Capital freed (exits)", marker_color="#1D9E75"))
fig2.update_layout(height=350, barmode="group", yaxis_title="₹", xaxis_title="Month", legend=dict(orientation="h", y=1.1))
st.plotly_chart(fig2, width="stretch")

st.subheader("Rotation-account balance (starting at ₹0, only reinvesting exit proceeds)")
fig3 = go.Figure()
fig3.add_trace(go.Scatter(x=monthly["month"], y=monthly["cumulative_balance"], mode="lines+markers", line=dict(color="#378ADD")))
fig3.add_hline(y=0, line_dash="dot", line_color="gray")
fig3.update_layout(height=350, yaxis_title="₹ (negative = shortfall needing infusion)", xaxis_title="Month")
st.plotly_chart(fig3, width="stretch")
st.caption(
    "Below zero means exits alone couldn't fund that month's entries - you'd have needed fresh money "
    f"beyond what rotation provided. The lowest point (₹{cf.min_cumulative_balance:,.0f}) is the minimum "
    "extra capital this strategy would have needed, historically, to never miss a signal."
)

st.divider()
st.subheader("Affordability simulator - can *your* capital keep up?")
st.caption(
    "Enter what you'd actually start with and add monthly. Trades are taken in chronological order; "
    "if cash runs short when a new entry signal fires, that trade is skipped (not queued) - matching "
    "how it would really play out."
)

col_a, col_b, col_c = st.columns(3)
starting_capital = col_a.number_input("Starting capital (₹)", min_value=0, value=300000, step=25000)
monthly_infusion = col_b.number_input("Monthly infusion (₹)", min_value=0, value=20000, step=5000)
window = col_c.selectbox(
    "Trade history to simulate against",
    ["Last 1 year (fresh start, most realistic)", "Last 2 years", "Last 3 years", "Full history since 2009 (long-run compounding)"],
)

trades = opt_result.trades.copy()
trades["entry_date"] = pd.to_datetime(trades["entry_date"])
cutoff_map = {
    "Last 1 year (fresh start, most realistic)": pd.Timestamp.now() - pd.DateOffset(years=1),
    "Last 2 years": pd.Timestamp.now() - pd.DateOffset(years=2),
    "Last 3 years": pd.Timestamp.now() - pd.DateOffset(years=3),
    "Full history since 2009 (long-run compounding)": None,
}
cutoff = cutoff_map[window]
sim_trades = trades[trades["entry_date"] >= cutoff].reset_index(drop=True) if cutoff is not None else trades

afford_clicked = st.button("🧮 Simulate affordability", type="primary")
if afford_clicked or "afford_result" not in st.session_state:
    afford = opt.simulate_affordability(sim_trades, starting_capital=starting_capital, monthly_infusion=monthly_infusion)
    st.session_state["afford_result"] = afford

afford: opt.AffordabilityResult = st.session_state["afford_result"]

if not afford.ok:
    st.warning(afford.message)
else:
    st.success(afford.message)
    c1, c2, c3 = st.columns(3)
    c1.metric("Trades taken", afford.taken_count)
    c2.metric("Trades skipped (no cash)", afford.skipped_count)
    c3.metric("Missed realized gains", f"₹{afford.missed_gain:,.0f}")

    fig4 = go.Figure()
    fig4.add_trace(go.Scatter(x=afford.balance_timeline["date"], y=afford.balance_timeline["cash_balance"], mode="lines", line=dict(color="#534AB7")))
    fig4.add_hline(y=0, line_dash="dot", line_color="gray")
    fig4.update_layout(height=300, yaxis_title="Cash balance (₹)", xaxis_title="Date")
    st.plotly_chart(fig4, width="stretch")

    with st.expander(f"Trade-by-trade breakdown ({len(afford.trades)})"):
        show_cols = ["symbol", "description", "entry_date", "entry_price", "afford_status", "status", "return_pct"]
        st.dataframe(
            afford.trades[show_cols],
            column_config={
                "symbol": "Symbol", "description": "Description", "entry_date": "Entry date",
                "entry_price": st.column_config.NumberColumn("Entry price", format="%.2f"),
                "afford_status": "Afforded?", "status": "Outcome",
                "return_pct": st.column_config.NumberColumn("Return %", format="%.0f%%"),
            },
            hide_index=True, width="stretch",
        )
