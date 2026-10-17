"""Streamlit page: scan one sector at a time with the Position Guide technicals and recommend."""

import sys
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.append(str(Path(__file__).resolve().parent.parent))
from strategies import position_guide as pg  # noqa: E402
from strategies import price_store  # noqa: E402
from strategies.config import get_setting  # noqa: E402
from strategies import sector_scanner as scanner  # noqa: E402
from strategies.holdings_style import BAD, GOOD, MID, style_holdings  # noqa: E402
from strategies.scan_ui import favourite_symbols, render_card, screener_url, tradingview_url  # noqa: E402

st.set_page_config(page_title="Sector Scanner", page_icon="🔭", layout="wide")

from strategies.auth_gate import require_password  # noqa: E402

require_password()
st.title("🔭 Sector Scanner")
st.caption(
    "Pulls every NSE-listed stock in a sector, runs the same technical checks as the Position Guide "
    "(relative strength, stabilisation, accumulation, volatility, volume profile, overhead supply) and "
    "gives each stock a call. Sectors are added one at a time as each gets frozen. Technicals only - check "
    "fundamentals before buying. Not investment advice."
)

DEFAULT_SHEET = get_setting("POSITIONS_SHEET_URL", "")

REC_STYLES = {
    "Accumulate": GOOD,
    "Strong - wait for dip": MID,
    "Watch - base forming": MID,
    "Avoid for now": BAD,
    "Price break - check": MID,
}


@st.cache_data(ttl=86400, show_spinner="Loading sector universe...")
def _cached_universe(sector: str, nonce: int):
    return scanner.load_universe(sector, refresh=nonce > 0)


@st.cache_data(ttl=3600, show_spinner="Updating stored price history (new sessions only) and recalculating...")
def _cached_stats(symbols: tuple, nonce: int):
    return pg.fetch_market_stats(list(symbols), force=nonce > 0)


@st.cache_data(ttl=300, show_spinner="Checking which of these you own...")
def _cached_owned(source: str):
    return set(pg.load_open_positions(source)["Stock"].dropna().unique())


if "scan_nonce" not in st.session_state:
    st.session_state.scan_nonce = 0

st.sidebar.header("Sector")
sector_names = list(scanner.SECTORS)
if st.session_state.get("goto_sector") in sector_names:  # arrived by clicking a row on the Sector Overview
    st.session_state["sector"] = st.session_state.pop("goto_sector")
elif st.session_state.get("sector") not in sector_names:
    st.session_state["sector"] = sector_names[0]
sector = st.sidebar.selectbox("Sector", sector_names, key="sector")
st.sidebar.page_link("pages/2_Sector_Overview.py", label="← All sectors overview")
st.sidebar.page_link("pages/4_Favourites.py", label=f"❤️ Favourites ({len(favourite_symbols())})")
st.sidebar.caption(scanner.SECTORS[sector]["note"])
if st.sidebar.button("🔄 Refresh universe + prices", type="primary", width="stretch"):
    st.session_state.scan_nonce += 1
    _cached_stats.clear()
    _cached_owned.clear()
nonce = st.session_state.scan_nonce

universe, universe_source = _cached_universe(sector, nonce)
stats = _cached_stats(tuple(sorted(universe["Stock"])), nonce)
try:
    owned = _cached_owned(DEFAULT_SHEET) if DEFAULT_SHEET else set()
except Exception:
    owned = set()

result = scanner.scan_sector(sector, stats, universe, owned)
if not result.ok:
    st.warning(result.message)
    st.stop()

t, s = result.table, result.summary
st.success(f"{result.message} ({universe_source})")
store = price_store.get_status(list(universe["Stock"]))
if store:
    st.caption(
        f"💾 Prices stored in {store['backend']} ({store['rows']:,} daily bars for {store['symbols']} stocks, latest session "
        f"{store['latest_session']}). Each run downloads only sessions newer than that and recalculates everything; "
        "🔄 Refresh forces a Yahoo re-check."
    )

# ---- sector health ----
st.header(f"{sector} - sector health")
c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Stocks with data", f"{s['with_data']} / {s['stocks']}")
c2.metric("Median RS 3M vs Nifty", f"{s['median_rs3m']:+.1%}" if pd.notna(s["median_rs3m"]) else "n/a")
c3.metric("Median drop from 52W high", f"{s['median_drop']:.0%}" if pd.notna(s["median_drop"]) else "n/a")
c4.metric("Above 200DMA", f"{s['above_200dma']:.0%}" if pd.notna(s["above_200dma"]) else "n/a")
c5.metric("Accumulate calls", s["accumulate"])

if pd.notna(s["median_rs3m"]):
    if s["median_rs3m"] > 0.02 and s["above_200dma"] >= 0.5:
        verdict = "🟢 **Sector is leading Nifty** - tailwind for adds, favour the best-scored names."
    elif s["median_rs3m"] < -0.02 and s["above_200dma"] < 0.5:
        verdict = "🔴 **Sector is lagging Nifty and mostly below its 200DMA** - be selective; only take stocks that are clearly basing."
    else:
        verdict = "🟡 **Sector is mixed** - stock selection matters more than the sector."
    st.markdown(verdict)

# ---- recommendations ----
st.header("Recommendations")
st.caption(
    "**Accumulate** = down 20%+ from the 52W high, no fresh low for 20+ sessions, Buy Score 65+ and liquid enough to build a position. "
    "**Strong - wait for dip** = Buy Score 65+ but not at a discount. **Watch - base forming** = Buy Score 45-65 (or a good setup that is too thinly traded). "
    "**Avoid for now** = Buy Score under 45, or making fresh lows. **Price break - check** = a one-day fall of 35%+ in the last year (usually a demerger Yahoo doesn't adjust for), so the numbers can't be trusted - check manually. ⭐ = top 3 Accumulate calls by Buy Score."
)


cards_tab, table_tab = st.tabs(["🃏 Cards (one stock per block)", "📊 Table"])
with cards_tab:
    for _, row in t.iterrows():
        render_card(row, sector)
with table_tab:
    cols = ["Pick", "Stock", "Company", "Owned", "Recommendation", "Buy Score", "Why", "CMP", "52W High Drop", "ATH Drop", "RS 1M", "RS 3M",
            "vs 200DMA", "Vol Trend", "Up/Down Vol", "Overhead Supply", "Supply to +25%", "RSI 14", "Days Since Low",
            "Volatility Ratio", "Off 52W Low", "vs POC", "VP Zone", "Avg Value 20D"]
    cols = [c for c in cols if c in t.columns]
    view = t[cols].copy()
    view.insert(cols.index("Stock") + 1, "TradingView", view["Stock"].map(tradingview_url))
    view.insert(cols.index("Stock") + 2, "Screener", view["Stock"].map(screener_url))


    def _style(df):
        styler = style_holdings(df)
        styler = styler.map(lambda v: REC_STYLES.get(v, ""), subset=["Recommendation"])
        return styler


    st.dataframe(
        _style(view),
        column_config={
            "TradingView": st.column_config.LinkColumn("TradingView", display_text="📈 Chart", help="Open the chart on TradingView"),
            "Screener": st.column_config.LinkColumn("Screener", display_text="📊 Screener", help="Open the company page on screener.in"),
            "CMP": st.column_config.NumberColumn(format="%.2f", help="Last completed daily close"),
            "Buy Score": st.column_config.NumberColumn(format="%.0f"),
            "52W High Drop": st.column_config.NumberColumn(format="percent"),
            "ATH Drop": st.column_config.NumberColumn(format="percent", help="From the all-time closing high (since listing). 0% = at its all-time high. Yahoo does not adjust for demergers, so a stock that demerged can show a bigger drop than a holder actually lost."),
            "RS 1M": st.column_config.NumberColumn(format="percent"),
            "RS 3M": st.column_config.NumberColumn(format="percent"),
            "vs 200DMA": st.column_config.NumberColumn(format="percent"),
            "Vol Trend": st.column_config.NumberColumn(format="%.2fx"),
            "Up/Down Vol": st.column_config.NumberColumn(format="%.2fx"),
            "Overhead Supply": st.column_config.NumberColumn(format="percent"),
            "Supply to +25%": st.column_config.NumberColumn(format="percent"),
            "RSI 14": st.column_config.NumberColumn(format="%.0f"),
            "Days Since Low": st.column_config.NumberColumn(format="%.0f"),
            "Volatility Ratio": st.column_config.NumberColumn(format="%.2fx"),
            "Off 52W Low": st.column_config.NumberColumn(format="percent"),
            "vs POC": st.column_config.NumberColumn(format="percent"),
            "Avg Value 20D": st.column_config.NumberColumn(format="₹%.1f cr", help="Average daily traded value, last 20 sessions"),
            "Why": st.column_config.TextColumn(width="large", help="✔ supports the call, ✖ works against it"),
        },
        hide_index=True, width="stretch",
    )
    st.download_button(
        "⬇️ Download scan as CSV",
        data=t[cols].to_csv(index=False).encode("utf-8"),
        file_name=f"{sector.lower().replace(' ', '_')}_scan.csv",
        mime="text/csv",
    )

with st.expander("How the call is made / how to read the columns"):
    st.markdown(
        """
Same indicators and colours as the Position Guide Holdings table (green = favours the stock, red = against, amber = in between).
The **Buy Score** (0-100) = stabilisation 25 + accumulation 25 + calm volatility 15 + strength vs Nifty 15 + clear path to +25% 20 - see the Position Guide for the full rationale.

The recommendation then adds two checks the score alone can't: **is it actually at a discount** (20%+ below the 52W high) and **is it liquid** (avg daily traded value above ₹0.5 cr). A stock still making fresh lows is marked *Avoid* however good the rest looks - with no stop-loss, a falling knife is the costly mistake.

**Series note:** stocks in the BE (trade-to-trade) segment, such as ESAF SFB, can only be bought for delivery with no intraday squaring - liquidity is usually thinner.
        """
    )
