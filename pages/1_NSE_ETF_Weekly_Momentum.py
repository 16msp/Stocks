"""Streamlit page: NSE ETF Weekly Momentum strategy."""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.express as px
import streamlit as st

sys.path.append(str(Path(__file__).resolve().parent.parent))
from strategies import nse_etf_momentum as strategy  # noqa: E402

st.set_page_config(page_title="NSE ETF Weekly Momentum", page_icon="📊", layout="wide")

SIGNAL_COLORS = {
    "BULLISH": "#2ecc71",
    "CAUTION": "#e74c3c",
    "FADING": "#f39c12",
    "QUIET": "#95a5a6",
}
SIGNAL_LABELS = {
    "BULLISH": "🟢 Bullish (vol up, price up)",
    "CAUTION": "🔴 Caution (vol up, price down)",
    "FADING": "🟠 Fading (vol down, price up)",
    "QUIET": "⚪ Quiet (vol down, price down)",
}

st.title("📊 NSE ETF Weekly Momentum")
st.caption(
    "Compares this week vs. last week for every NSE-listed ETF: rising volume + rising "
    "price = momentum building; rising volume + falling price = possible sell-off. "
    "Not investment advice - a data screen to narrow down what to look at."
)

# --------------------------------------------------------------------------
# DB status
# --------------------------------------------------------------------------

status = strategy.get_db_status()
c1, c2, c3, c4 = st.columns(4)
c1.metric("ETFs tracked", status.get("known_etfs", 0))
c2.metric("Trading days stored", status.get("day_count", 0))
c3.metric("Data from", status.get("first_date") or "-")
c4.metric("Data to", status.get("last_date") or "-")

# --------------------------------------------------------------------------
# Sidebar controls
# --------------------------------------------------------------------------

st.sidebar.header("Settings")
weeks = st.sidebar.slider("Weeks of history to compare", 2, 6, 2)
top_n = st.sidebar.slider("Rows per table", 5, 40, 15)
min_volume = st.sidebar.number_input(
    "Min prior-week volume (shares)", min_value=0, value=5000, step=1000,
    help="ETFs traded less than this in the prior week are excluded from the ranked tables "
    "(too thin for a % change to be meaningful).",
)

st.sidebar.divider()
fetch_clicked = st.sidebar.button("🔄 Fetch latest data", width="stretch")
analyze_clicked = st.sidebar.button("📈 Run analysis", type="primary", width="stretch")

# --------------------------------------------------------------------------
# Fetch
# --------------------------------------------------------------------------

if fetch_clicked:
    log_box = st.status("Fetching live NSE ETF list + price history...", expanded=True)
    result = strategy.fetch(progress=lambda msg: log_box.write(msg))
    if result.fetched:
        log_box.update(label=f"Done - {result.message}", state="complete", expanded=False)
    else:
        log_box.update(label=result.message or "Nothing new to fetch.", state="complete", expanded=False)
    st.rerun()

# --------------------------------------------------------------------------
# Analyze (auto-run once if data exists and nothing analyzed yet this session)
# --------------------------------------------------------------------------

if analyze_clicked or ("analysis" not in st.session_state and status.get("exists")):
    st.session_state["analysis"] = strategy.analyze(weeks=weeks, top=top_n, min_volume=min_volume)

result = st.session_state.get("analysis")

if not status.get("exists"):
    st.warning("No data stored yet. Click **Fetch latest data** in the sidebar to get started.")
elif result is None:
    st.info("Click **Run analysis** in the sidebar to compute this week's trend.")
elif not result.ok:
    st.warning(result.message)
else:
    st.success(result.message)

    if not result.bad_ticks.empty:
        with st.expander(f"⚠️ {len(result.bad_ticks)} suspected bad tick(s) dropped from analysis"):
            st.dataframe(result.bad_ticks[["date", "symbol", "close"]], hide_index=True, width="stretch")

    pct_cols = {
        "price_change_pct": st.column_config.NumberColumn("Price %", format="%.2f%%"),
        "volume_change_pct": st.column_config.NumberColumn("Volume %", format="%.2f%%"),
        "this_week_close": st.column_config.NumberColumn("Close", format="%.2f"),
        "this_week_volume": st.column_config.NumberColumn("This Week Vol", format="%,d"),
        "prev_week_volume": st.column_config.NumberColumn("Prev Week Vol", format="%,d"),
        "symbol": "Symbol",
        "category": "Category",
    }
    show_cols = ["symbol", "category", "price_change_pct", "volume_change_pct", "this_week_close", "this_week_volume"]

    col_bull, col_caution = st.columns(2)
    with col_bull:
        st.subheader("🟢 Bullish momentum")
        st.caption("Volume up + price up")
        if result.bullish.empty:
            st.write("(none)")
        else:
            st.dataframe(result.bullish[show_cols], column_config=pct_cols, hide_index=True, width="stretch")

    with col_caution:
        st.subheader("🔴 Caution")
        st.caption("Heavy volume, price falling")
        if result.caution.empty:
            st.write("(none)")
        else:
            st.dataframe(result.caution[show_cols], column_config=pct_cols, hide_index=True, width="stretch")

    with st.expander("Other signals: Fading (vol down, price up) & Quiet (vol down, price down)"):
        col_f, col_q = st.columns(2)
        with col_f:
            st.write("**🟠 Fading**")
            st.dataframe(result.fading[show_cols], column_config=pct_cols, hide_index=True, width="stretch")
        with col_q:
            st.write("**⚪ Quiet**")
            st.dataframe(result.quiet[show_cols], column_config=pct_cols, hide_index=True, width="stretch")

    # ----------------------------------------------------------------
    # Quadrant scatter chart of all liquid ETFs
    # ----------------------------------------------------------------
    st.subheader("Volume % vs Price % - all liquid ETFs")
    liquid = result.full[result.full["liquid"]].copy()
    if not liquid.empty:
        clip_x = 300  # extreme outliers get clipped for chart readability only
        liquid["volume_change_pct_display"] = np.clip(liquid["volume_change_pct"], -100, clip_x)
        fig = px.scatter(
            liquid,
            x="volume_change_pct_display",
            y="price_change_pct",
            color="signal",
            color_discrete_map=SIGNAL_COLORS,
            hover_name="symbol",
            hover_data={"category": True, "volume_change_pct": ":.1f", "price_change_pct": ":.2f", "volume_change_pct_display": False},
            labels={"volume_change_pct_display": "Volume change % (clipped at 300% for display)", "price_change_pct": "Price change %"},
        )
        fig.add_hline(y=0, line_dash="dot", line_color="gray")
        fig.add_vline(x=0, line_dash="dot", line_color="gray")
        fig.update_layout(height=500, legend_title_text="Signal")
        st.plotly_chart(fig, width="stretch")
        st.caption(
            "Top-right = bullish momentum, bottom-right = caution/sell-off. "
            "X-axis clipped at 300% for readability - exact values are in the tables/CSV."
        )

    # ----------------------------------------------------------------
    # Full data + download
    # ----------------------------------------------------------------
    with st.expander(f"Full ranked list ({len(result.full)} ETFs)"):
        st.dataframe(result.full[["symbol", "category", "signal"] + show_cols[2:]], column_config=pct_cols, hide_index=True, width="stretch")

    st.download_button(
        "⬇️ Download full results as CSV",
        data=result.full.to_csv(index=False).encode("utf-8"),
        file_name=f"nse_etf_trend_{result.this_week}.csv",
        mime="text/csv",
    )
