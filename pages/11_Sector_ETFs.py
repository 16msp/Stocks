"""Streamlit page: browse each NSE sector's own index ETF.

One representative ETF per sector (the most-liquid when several track the same
index), shown with the same card as the Sector Scanner stocks plus a
price-against-the-all-time-high chart like the Dip Watchlist.
"""

import sys
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

sys.path.append(str(Path(__file__).resolve().parent.parent))
from strategies import position_guide as pg  # noqa: E402
from strategies import price_store  # noqa: E402
from strategies import sector_scanner as scanner  # noqa: E402
from strategies.scan_ui import render_card  # noqa: E402

st.set_page_config(page_title="Sector ETFs", page_icon="📦", layout="wide")

from strategies.auth_gate import require_password  # noqa: E402

require_password()
st.title("📦 Sector ETFs")
st.caption(
    "Each NSE sector's own index ETF - the most-liquid one when several track the same index - "
    "shown with the same card as the Sector Scanner stocks, plus its price against the all-time high. "
    "Technicals only - not investment advice."
)

if "etf_nonce" not in st.session_state:
    st.session_state.etf_nonce = 0


@st.cache_data(ttl=3600, show_spinner="Resolving each sector's index ETF...")
def _cached_sector_etfs(nonce: int):
    return scanner.all_sector_etfs()


@st.cache_data(ttl=3600, show_spinner="Scoring every sector ETF...")
def _cached_etf_overview(nonce: int):
    """One sortable row per sector ETF: returns vs Nifty, RSI, trend, drop from
    the all-time high and the Buy Score, so opportunities can be sorted out."""
    etf_map = scanner.all_sector_etfs()
    syms = list(etf_map.values())
    if not syms:
        return pd.DataFrame()
    price_store.update_ath(syms, max_age_days=7)  # so "ATH Drop" is a true all-time figure
    ms = pg.fetch_market_stats(syms, force=nonce > 0)
    uni = pd.DataFrame({"Stock": syms, "Company": [f"{g} index ETF" for g in etf_map]})
    res = scanner.scan_sector("Sector ETFs", ms, uni)
    if not res.ok or res.table.empty:
        return pd.DataFrame()
    t = res.table.copy()
    t["Sector"] = t["Stock"].map({sym: g for g, sym in etf_map.items()})
    return t.rename(columns={"Stock": "ETF", "ATH Drop": "Drop from ATH"})


@st.cache_data(ttl=3600, show_spinner="Updating prices and recalculating...")
def _cached_etf_card(symbol: str, label: str, nonce: int):
    ms = pg.fetch_market_stats([symbol], force=nonce > 0)
    uni = pd.DataFrame({"Stock": [symbol], "Company": [label]})
    res = scanner.scan_sector(label, ms, uni)
    return res.table if res.ok and not res.table.empty else None


@st.cache_data(ttl=3600, show_spinner="Loading price history...")
def _cached_etf_history(symbol: str, nonce: int):
    price_store.update_ath([symbol], max_age_days=7)
    hist = price_store.load_history([symbol]).get(symbol)
    ath = price_store.load_ath([symbol]).get(symbol)
    return hist, ath


if st.sidebar.button("🔄 Refresh prices", type="primary", width="stretch"):
    st.session_state.etf_nonce += 1
    _cached_sector_etfs.clear()
    _cached_etf_card.clear()
    _cached_etf_history.clear()
nonce = st.session_state.etf_nonce

etf_map = _cached_sector_etfs(nonce)
if not etf_map:
    st.warning("No sector ETFs available yet. Prices may not have been fetched - try Refresh prices.")
    st.stop()

# ---- sortable overview of every sector ETF ----
st.subheader("All sector ETFs")
st.caption("Sort any column to find opportunities - e.g. the most below the all-time high, "
           "the strongest relative strength, or the highest Buy Score.")
overview = _cached_etf_overview(nonce)
if overview.empty:
    st.info("No sector ETFs scored yet. Try Refresh prices.")
else:
    ov_cols = ["Sector", "ETF", "Recommendation", "Buy Score", "CMP", "Drop from ATH",
               "52W High Drop", "RS 1M", "RS 3M", "RSI 14", "vs 200DMA", "Vol Trend", "Avg Value 20D"]
    ov_cols = [c for c in ov_cols if c in overview.columns]
    st.dataframe(
        overview[ov_cols],
        column_config={
            "Buy Score": st.column_config.NumberColumn(format="%.0f", help="0-100 timing score"),
            "CMP": st.column_config.NumberColumn(format="%.2f", help="Last completed daily close"),
            "Drop from ATH": st.column_config.NumberColumn(format="percent", help="Last close against the all-time closing high"),
            "52W High Drop": st.column_config.NumberColumn(format="percent"),
            "RS 1M": st.column_config.NumberColumn(format="percent", help="1-month return minus Nifty 50's"),
            "RS 3M": st.column_config.NumberColumn(format="percent", help="3-month return minus Nifty 50's"),
            "RSI 14": st.column_config.NumberColumn(format="%.0f"),
            "vs 200DMA": st.column_config.NumberColumn(format="percent"),
            "Vol Trend": st.column_config.NumberColumn(format="%.2fx"),
            "Avg Value 20D": st.column_config.NumberColumn(format="₹%.1f cr", help="Average daily traded value, last 20 sessions"),
        },
        hide_index=True, width="stretch", height=430,
    )
    st.download_button("⬇️ Download sector ETFs as CSV",
                       overview[ov_cols].to_csv(index=False).encode("utf-8"),
                       "sector_etfs.csv", "text/csv")

# ---- drill down into one ETF ----
st.divider()
st.subheader("Drill into one ETF")
labels = {f"{group} — {sym}": (group, sym) for group, sym in etf_map.items()}
choice = st.selectbox("Sector ETF", list(labels))
group, sym = labels[choice]

etf_table = _cached_etf_card(sym, f"{group} index ETF", nonce)
if etf_table is None:
    st.warning(f"No price history yet for {sym}.")
    st.stop()

render_card(etf_table.iloc[0], sector=group, key_prefix="etfcard")

# ---- price against the all-time high ----
st.subheader("Price against the all-time high")
hist, ath = _cached_etf_history(sym, nonce)
close = hist["Close"].dropna() if hist is not None else pd.Series(dtype=float)
if close.empty:
    st.info("No stored price history for this ETF yet.")
else:
    last = float(close.iloc[-1])
    ath_val, ath_date = (ath if ath else (float(close.max()), str(close.idxmax().date())))
    ath_val = max(ath_val, float(close.max()))  # a newer stored high wins over a stale stored ATH
    drop = last / ath_val - 1.0

    fig = go.Figure(go.Scatter(x=close.index, y=close.values, mode="lines", name="Close", line=dict(width=2)))
    dma = close.rolling(200).mean().dropna()
    if not dma.empty:
        fig.add_trace(go.Scatter(x=dma.index, y=dma.values, mode="lines", name="200-day average",
                                 line=dict(width=1.5, color="#7f7f7f", dash="dash")))
    for pct, colour in [(0, "#2e7d32"), (0.35, "#b7950b"), (0.45, "#d35400"), (0.55, "#c0392b")]:
        fig.add_hline(y=ath_val * (1 - pct), line=dict(color=colour, dash="dot" if pct else "solid", width=1),
                      annotation_text="all-time high" if pct == 0 else f"{pct:.0%} below", annotation_position="top left")
    fig.update_layout(height=380, margin=dict(l=0, r=0, t=10, b=0), showlegend=True,
                      legend=dict(orientation="h", y=1.08), yaxis_title="₹")
    st.plotly_chart(fig, use_container_width=True)
    try:
        ath_when = pd.to_datetime(ath_date).strftime("%d %b %Y")
    except Exception:
        ath_when = str(ath_date)
    st.caption(f"{sym}: {drop:.1%} below its all-time high of ₹{ath_val:,.2f} ({ath_when}). "
               "The chart shows the stored sessions only (about 15 months).")
