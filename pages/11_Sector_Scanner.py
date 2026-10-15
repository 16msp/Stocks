"""Streamlit page: scan one sector at a time with the Position Guide technicals and recommend."""

import sys
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.append(str(Path(__file__).resolve().parent.parent))
from strategies import position_guide as pg  # noqa: E402
from strategies import price_store  # noqa: E402
from strategies import sector_scanner as scanner  # noqa: E402
from strategies.holdings_style import BAD, GOOD, MID, style_holdings  # noqa: E402

st.set_page_config(page_title="Sector Scanner", page_icon="🔭", layout="wide")
st.title("🔭 Sector Scanner")
st.caption(
    "Pulls every NSE-listed stock in a sector, runs the same technical checks as the Position Guide "
    "(relative strength, stabilisation, accumulation, volatility, volume profile, overhead supply) and "
    "gives each stock a call. Sectors are added one at a time as each gets frozen. Technicals only - check "
    "fundamentals before buying. Not investment advice."
)

DEFAULT_SHEET = "https://docs.google.com/spreadsheets/d/1pl4T2Avhm4Ks39rQS3ocIkpeQ2RCyTeIzaCP_q9jyWw/edit?usp=sharing"

REC_STYLES = {
    "Accumulate": GOOD,
    "Strong - wait for dip": MID,
    "Watch - base forming": MID,
    "Avoid for now": BAD,
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
sector = st.sidebar.selectbox("Sector", list(scanner.SECTORS), index=0)
st.sidebar.caption(scanner.SECTORS[sector]["note"])
if st.sidebar.button("🔄 Refresh universe + prices", type="primary", width="stretch"):
    st.session_state.scan_nonce += 1
    _cached_stats.clear()
    _cached_owned.clear()
nonce = st.session_state.scan_nonce

universe, universe_source = _cached_universe(sector, nonce)
stats = _cached_stats(tuple(sorted(universe["Stock"])), nonce)
try:
    owned = _cached_owned(DEFAULT_SHEET)
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
        f"💾 Prices are stored locally ({store['rows']:,} daily bars for {store['symbols']} stocks, latest session "
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
    "**Avoid for now** = Buy Score under 45, or making fresh lows. ⭐ = top 3 Accumulate calls by Buy Score."
)


REC_ICON = {"Accumulate": "🟢", "Strong - wait for dip": "🟡", "Watch - base forming": "🟡", "Avoid for now": "🔴", "Insufficient data": "⚪"}


def _fmt(v, kind):
    if v is None or pd.isna(v):
        return "n/a"
    return {"pct": f"{v:+.1%}", "pct0": f"{v:.0%}", "x": f"{v:.2f}x", "int": f"{v:.0f}", "px": f"{v:,.2f}"}[kind]


def _chip(label, value, style):
    """Coloured inline chip: label on top-line, value in a tinted box."""
    return f'<span style="{style};padding:1px 6px;border-radius:6px;margin-right:4px;white-space:nowrap;font-size:0.85em">{label}: <b>{value}</b></span>'


def _style_for(value, good, bad, higher=True):
    if value is None or pd.isna(value):
        return ""
    v, g, b = (value, good, bad) if higher else (-value, -good, -bad)
    return GOOD if v >= g else (BAD if v <= b else MID)


def render_card(r):
    with st.container(border=True):
        head, body, why = st.columns([1.5, 3.0, 2.0])
        with head:
            owned = f" &nbsp; {r['Owned']}" if r["Owned"] else ""
            pick = f"{r['Pick']} &nbsp; " if r["Pick"] else ""
            st.markdown(f"### {pick}{r['Stock']}{owned}")
            st.caption(r["Company"])
            st.markdown(f"{REC_ICON.get(r['Recommendation'], '')} **{r['Recommendation']}**")
            st.metric("Buy Score", _fmt(r["Buy Score"], "int"), help="0-100 timing score")
        with body:
            st.markdown(
                f"**CMP ₹{_fmt(r['CMP'], 'px')}** &nbsp;|&nbsp; 52W high drop **{_fmt(r['52W High Drop'], 'pct0')}** "
                f"&nbsp;|&nbsp; bounce off low **{_fmt(r['Off 52W Low'], 'pct0')}** &nbsp;|&nbsp; RSI **{_fmt(r['RSI 14'], 'int')}**"
            )
            st.markdown(
                _chip("RS 1M", _fmt(r["RS 1M"], "pct"), _style_for(r["RS 1M"], 0.02, -0.02))
                + _chip("RS 3M", _fmt(r["RS 3M"], "pct"), _style_for(r["RS 3M"], 0.02, -0.02))
                + _chip("vs 200DMA", _fmt(r["vs 200DMA"], "pct"), _style_for(r["vs 200DMA"], 0.0, -0.10)),
                unsafe_allow_html=True,
            )
            st.markdown(
                _chip("Up/Down vol", _fmt(r["Up/Down Vol"], "x"), _style_for(r["Up/Down Vol"], 1.2, 0.8))
                + _chip("Vol trend", _fmt(r["Vol Trend"], "x"), _style_for(r["Vol Trend"], 1.2, 0.7))
                + _chip("Days since low", _fmt(r["Days Since Low"], "int"), _style_for(r["Days Since Low"], 25, 10)),
                unsafe_allow_html=True,
            )
            st.markdown(
                _chip("Overhead supply", _fmt(r["Overhead Supply"], "pct0"), _style_for(r["Overhead Supply"], 0.30, 0.70, higher=False))
                + _chip("Supply to +25%", _fmt(r["Supply to +25%"], "pct0"), _style_for(r["Supply to +25%"], 0.40, 0.70, higher=False))
                + _chip("Volatility", _fmt(r["Volatility Ratio"], "x"), _style_for(r["Volatility Ratio"], 0.85, 1.15, higher=False)),
                unsafe_allow_html=True,
            )
            st.caption(f"Volume-profile zone: {r['VP Zone'] or 'n/a'} (CMP {_fmt(r['vs POC'], 'pct')} vs POC) · avg traded ₹{_fmt(r['Avg Value 20D'], 'px')} cr/day")
        with why:
            good, _, bad = r["Why"].partition(" | ")
            if not r["Why"].startswith("✔"):
                good, bad = "", r["Why"]
            for item in [g for g in good.removeprefix("✔ ").split("; ") if g]:
                st.markdown(f"✔ {item}")
            for item in [b for b in bad.removeprefix("✖ ").split("; ") if b]:
                st.markdown(f"✖ {item}")


cards_tab, table_tab = st.tabs(["🃏 Cards (one stock per block)", "📊 Table"])
with cards_tab:
    for _, row in t.iterrows():
        render_card(row)
with table_tab:
    cols = ["Pick", "Stock", "Company", "Owned", "Recommendation", "Buy Score", "Why", "CMP", "52W High Drop", "RS 1M", "RS 3M",
            "vs 200DMA", "Vol Trend", "Up/Down Vol", "Overhead Supply", "Supply to +25%", "RSI 14", "Days Since Low",
            "Volatility Ratio", "Off 52W Low", "vs POC", "VP Zone", "Avg Value 20D"]
    cols = [c for c in cols if c in t.columns]
    view = t[cols]


    def _style(df):
        styler = style_holdings(df)
        styler = styler.map(lambda v: REC_STYLES.get(v, ""), subset=["Recommendation"])
        return styler


    st.dataframe(
        _style(view),
        column_config={
            "CMP": st.column_config.NumberColumn(format="%.2f", help="Last completed daily close"),
            "Buy Score": st.column_config.NumberColumn(format="%.0f"),
            "52W High Drop": st.column_config.NumberColumn(format="percent"),
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
