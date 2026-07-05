"""Streamlit page: correlation-based basket segregation for the ATH-drop averaging universe."""

import sys
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.append(str(Path(__file__).resolve().parent.parent))
from strategies import ath_correlation as corr  # noqa: E402
from strategies import ath_optimizer as opt  # noqa: E402
from strategies import nse_etf_momentum as base  # noqa: E402

st.set_page_config(page_title="ETF Correlation Baskets", page_icon="🧺", layout="wide")

st.title("🧺 ETF Correlation Baskets")
st.caption(
    "Many ETFs in the universe move together - trading all of them isn't diversification, it's the "
    "same bet repeated, which is why entries cluster and capital demand spikes during market-wide moves "
    "(see Cash Flow Planner). This groups ETFs into correlation-based baskets (hierarchical clustering "
    "on daily return correlation, complete linkage so one chain of loosely-related pairs can't drag "
    "unrelated ETFs into one giant blob), picks the highest-volume ETF per basket, and compares trading "
    "everything vs. trading one representative per basket. Not investment advice."
)

status = base.get_db_status()
if not status.get("exists"):
    st.warning("No data stored yet. Go to the **NSE ETF Weekly Momentum** page and click **Fetch latest data** first.")
    st.stop()

search_query = st.text_input("🔍 Search (symbol or description) - filters the basket table", key="corr_search")


def _filter(df, cols=("symbol", "description")):
    if not search_query or df.empty:
        return df
    present = [c for c in cols if c in df.columns]
    mask = False
    for c in present:
        mask = mask | df[c].astype(str).str.contains(search_query, case=False, na=False)
    return df[mask]


st.sidebar.header("Clustering settings")
distance_threshold = st.sidebar.slider(
    "Distance threshold (lower = stricter baskets)", 0.1, 0.6, corr.DEFAULT_DISTANCE_THRESHOLD, step=0.05,
    help="Two ETFs merge into the same basket while (1 - correlation) is below this. 0.3 means correlation >= 0.70 to merge.",
)
run_clicked = st.sidebar.button("🧺 Run clustering", type="primary", width="stretch")

if run_clicked or "cluster_result" not in st.session_state:
    with st.spinner("Computing correlations and clustering..."):
        st.session_state["cluster_result"] = corr.cluster_etfs(distance_threshold=distance_threshold)

cluster_result: corr.ClusterResult = st.session_state["cluster_result"]

if not cluster_result.ok:
    st.warning(cluster_result.message)
    st.stop()

st.success(cluster_result.message)

assignments = cluster_result.assignments
basket_sizes = assignments.groupby("cluster_id").size()
c1, c2, c3 = st.columns(3)
c1.metric("ETFs clustered", len(assignments))
c2.metric("Baskets", cluster_result.n_clusters)
c3.metric("Largest basket", int(basket_sizes.max()))

st.subheader("Basket assignments")
st.dataframe(
    _filter(assignments),
    column_config={
        "symbol": "Symbol", "description": "Description", "cluster_id": "Basket",
        "avg_volume": st.column_config.NumberColumn("Avg volume (90d)", format="%d"),
        "is_representative": st.column_config.CheckboxColumn("Representative (traded)"),
    },
    hide_index=True, width="stretch",
)

st.divider()
st.subheader("Full universe vs. diversified (one ETF per basket)")
compare_clicked = st.button("⚖️ Run comparison (backtests both universes)", type="primary")

if compare_clicked or "corr_compare_result" not in st.session_state:
    with st.spinner("Backtesting both universes..."):
        diversified_symbols = corr.get_diversified_symbol_list(cluster_result)
        opt_full = opt.run_optimizer()
        opt_div = opt.run_optimizer(symbols=diversified_symbols)
        cf_full = opt.analyze_cashflow(opt_full.trades)
        cf_div = opt.analyze_cashflow(opt_div.trades)
        st.session_state["corr_compare_result"] = (opt_full, opt_div, cf_full, cf_div)

if "corr_compare_result" in st.session_state:
    opt_full, opt_div, cf_full, cf_div = st.session_state["corr_compare_result"]

    comparison = pd.DataFrame({
        "Metric": ["ETFs traded", "Total resolved trades", "Peak concurrent tranches", "Peak capital required",
                   "Avg concurrent tranches", "Total realized gain"],
        "Full universe": [
            str(opt_full.trades["symbol"].nunique()), str((opt_full.trades["status"] == "WIN").sum()),
            str(cf_full.peak_concurrent), f"₹{cf_full.peak_capital:,.0f}",
            f"{cf_full.avg_concurrent:.1f}", f"₹{opt_full.universe['total_gain'].sum():,.0f}",
        ],
        "Diversified (1/basket)": [
            str(opt_div.trades["symbol"].nunique()), str((opt_div.trades["status"] == "WIN").sum()),
            str(cf_div.peak_concurrent), f"₹{cf_div.peak_capital:,.0f}",
            f"{cf_div.avg_concurrent:.1f}", f"₹{opt_div.universe['total_gain'].sum():,.0f}",
        ],
    })
    st.dataframe(comparison, hide_index=True, width="stretch")
    st.caption(
        f"Diversifying cuts peak capital requirement by "
        f"{(1 - cf_div.peak_capital / cf_full.peak_capital) * 100:.0f}% - but total return also drops "
        "since you're deliberately taking fewer, less-redundant positions. The real question is which "
        "wins with the *same limited capital* - see the affordability comparison below."
    )

    st.subheader("Same capital, both universes - which wins?")
    st.caption(
        "Tests recent signals only (fresh-start realism, not 17 years of compounding) with identical "
        "starting capital and monthly infusion for both universes."
    )
    col_a, col_b, col_c = st.columns(3)
    capital = col_a.number_input("Starting capital (₹)", min_value=0, value=200000, step=25000, key="corr_capital")
    monthly = col_b.number_input("Monthly infusion (₹)", min_value=0, value=15000, step=5000, key="corr_monthly")
    years_back = col_c.selectbox("Recent window", [1, 2, 3], index=1, key="corr_years")

    def _recent(trades, years):
        t = trades.copy()
        t["entry_date"] = pd.to_datetime(t["entry_date"])
        return t[t["entry_date"] >= pd.Timestamp.now() - pd.DateOffset(years=years)].reset_index(drop=True)

    full_recent = _recent(opt_full.trades, years_back)
    div_recent = _recent(opt_div.trades, years_back)

    afford_full = opt.simulate_affordability(full_recent, capital, monthly)
    afford_div = opt.simulate_affordability(div_recent, capital, monthly)

    def _realized_gain(afford_result):
        taken_wins = afford_result.trades[afford_result.trades["taken"] & (afford_result.trades["status"] == "WIN")]
        return (taken_wins["return_pct"] / 100 * opt.TRADE_SIZE).sum()

    full_gain = _realized_gain(afford_full)
    div_gain = _realized_gain(afford_div)

    result_df = pd.DataFrame({
        "": ["Signals available", "Trades taken", "Trades skipped (no cash)", "Realized gain"],
        "Full universe": [str(len(full_recent)), str(afford_full.taken_count), str(afford_full.skipped_count), f"₹{full_gain:,.0f}"],
        "Diversified (1/basket)": [str(len(div_recent)), str(afford_div.taken_count), str(afford_div.skipped_count), f"₹{div_gain:,.0f}"],
    })
    st.dataframe(result_df, hide_index=True, width="stretch")

    winner = "Diversified" if div_gain > full_gain else "Full universe" if full_gain > div_gain else "Tie"
    st.info(f"With ₹{capital:,.0f} + ₹{monthly:,.0f}/month over the last {years_back} year(s): **{winner}** produced more realized gain.")
