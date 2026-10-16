"""Streamlit page: one row per sector so you can see which sectors are healthy, then click through to the scan."""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import streamlit as st

sys.path.append(str(Path(__file__).resolve().parent.parent))
from strategies import position_guide as pg  # noqa: E402
from strategies import price_store  # noqa: E402
from strategies import sector_scanner as scanner  # noqa: E402
from strategies.holdings_style import BAD, GOOD, MID  # noqa: E402

st.set_page_config(page_title="Sector Overview", page_icon="🧭", layout="wide")

from strategies.auth_gate import require_password  # noqa: E402

require_password()
st.title("🧭 Sector Overview")
st.caption(
    "Every sector on one screen: how healthy it is and which stocks look best. **Click a row to open that sector's "
    "full scan.** Uses the stored price history only (the daily job keeps it current), so nothing is downloaded here. "
    "Technicals only - check fundamentals before buying. Not investment advice."
)

LIQUID_CR = 10  # a pick must trade at least this much (Rs crore/day) to be listed as a liquid pick


def _health(rs3m, above200):
    if pd.isna(rs3m) or pd.isna(above200):
        return "n/a"
    if rs3m > 0.02 and above200 >= 0.5:
        return "🟢 Leading"
    if rs3m < -0.02 and above200 < 0.5:
        return "🔴 Lagging"
    return "🟡 Mixed"


@st.cache_data(ttl=3600, show_spinner="Scanning all sectors from the stored prices (about 15 seconds the first time)...")
def _overview(nonce: int) -> pd.DataFrame:
    universes = {name: scanner.load_universe(name)[0] for name in scanner.SECTORS}
    symbols = sorted({s for u in universes.values() for s in u["Stock"]})
    stats = pg.fetch_market_stats(symbols, update=False)
    rows = []
    for name, uni in universes.items():
        res = scanner.scan_sector(name, stats, uni, owned=set())
        if not res.ok:
            continue
        t, s = res.table, res.summary
        liquid = t[(t["Recommendation"] == "Accumulate") & (t["Avg Value 20D"] >= LIQUID_CR)]
        liquid = liquid.sort_values("Buy Score", ascending=False).head(3)
        rows.append({
            "Sector": name,
            "Stocks": s["with_data"],
            "Health": _health(s["median_rs3m"], s["above_200dma"]),
            "Median RS 3M": s["median_rs3m"],
            "Above 200DMA": s["above_200dma"],
            "Median 52W Drop": s["median_drop"],
            "Median Buy Score": s["median_score"],
            "Accumulate": s["accumulate"],
            "Wait for dip": int((t["Recommendation"] == "Strong - wait for dip").sum()),
            "Avoid": int((t["Recommendation"] == "Avoid for now").sum()),
            "Best liquid Accumulate picks": ", ".join(f"{r.Stock} {r['Buy Score']:.0f}" for _, r in liquid.iterrows()) or "-",
        })
    out = pd.DataFrame(rows)
    return out.sort_values("Median Buy Score", ascending=False).reset_index(drop=True)


if "overview_nonce" not in st.session_state:
    st.session_state.overview_nonce = 0

st.sidebar.header("Sector Overview")
if st.sidebar.button("🔄 Recalculate", width="stretch"):
    st.session_state.overview_nonce += 1
    _overview.clear()

df = _overview(st.session_state.overview_nonce)
store = price_store.get_status(sorted({s for name in scanner.SECTORS for s in scanner.SECTORS[name].get("symbols", [])}))
if store:
    st.caption(f"💾 Prices stored in {store['backend']}, latest session {store['latest_session']}.")

c1, c2, c3, c4 = st.columns(4)
c1.metric("Sectors", len(df))
c2.metric("🟢 Leading", int(df["Health"].str.contains("Leading").sum()))
c3.metric("🟡 Mixed", int(df["Health"].str.contains("Mixed").sum()))
c4.metric("🔴 Lagging", int(df["Health"].str.contains("Lagging").sum()))


def _color(v, good, bad, higher=True):
    if v is None or pd.isna(v):
        return ""
    x, g, b = (v, good, bad) if higher else (-v, -good, -bad)
    return GOOD if x >= g else (BAD if x <= b else MID)


def _style(frame: pd.DataFrame):
    styler = frame.style
    styler = styler.map(lambda v: _color(v, 0.02, -0.02), subset=["Median RS 3M"])
    styler = styler.map(lambda v: _color(v, 0.55, 0.35), subset=["Above 200DMA"])
    styler = styler.map(lambda v: _color(v, -0.15, -0.30), subset=["Median 52W Drop"])
    styler = styler.map(lambda v: _color(v, 65, 40), subset=["Median Buy Score"])
    styler = styler.map(lambda v: GOOD if "Leading" in v else (BAD if "Lagging" in v else MID if "Mixed" in v else ""), subset=["Health"])
    return styler


st.subheader("All sectors, best first")
event = st.dataframe(
    _style(df),
    column_config={
        "Stocks": st.column_config.NumberColumn(help="Stocks with enough price history"),
        "Median RS 3M": st.column_config.NumberColumn(format="percent", help="Median 3-month return minus Nifty's"),
        "Above 200DMA": st.column_config.NumberColumn(format="percent", help="Share of stocks trading above their 200-day average"),
        "Median 52W Drop": st.column_config.NumberColumn(format="percent", help="Median distance below the 52-week high"),
        "Median Buy Score": st.column_config.NumberColumn(format="%.0f", help="Median 0-100 timing score"),
        "Accumulate": st.column_config.NumberColumn(help="Stocks in a base, at a discount and being bought"),
        "Wait for dip": st.column_config.NumberColumn(help="Strong score but not at a discount yet"),
        "Avoid": st.column_config.NumberColumn(help="Weak score or making fresh lows"),
        "Best liquid Accumulate picks": st.column_config.TextColumn(
            width="large", help=f"Top Accumulate calls by Buy Score that trade at least Rs {LIQUID_CR} cr/day"
        ),
    },
    hide_index=True,
    width="stretch",
    height=35 * (len(df) + 1) + 3,  # show every sector without an inner scrollbar
    on_select="rerun",
    selection_mode="single-row",
    key="sector_overview_table",
)

picked = None
if event.selection.rows:
    picked = df.iloc[event.selection.rows[0]]["Sector"]

st.caption("Click a row (or use the box below) to open that sector's full scan. Use the table's search icon or column headers to filter and sort.")
left, right = st.columns([3, 1])
choice = left.selectbox("Open a sector", list(df["Sector"]), index=None, placeholder="Choose a sector...", label_visibility="collapsed")
if right.button("Open sector ➜", width="stretch", disabled=choice is None):
    picked = choice

if picked:
    st.session_state["goto_sector"] = picked
    st.switch_page("pages/3_Sector_Scanner.py")

with st.expander("How to read this"):
    st.markdown(
        """
- **Health** - 🟢 Leading = the median stock beats Nifty by 2 points or more over 3 months *and* at least half the stocks are above their 200-day average. 🔴 Lagging = the median stock trails by 2 points or more and fewer than half are above it. Anything else is 🟡 Mixed.
- **Median Buy Score** - the middle stock's 0-100 timing score. Above 65 is a healthy sector; below 40 is a weak one. Sectors are sorted by this.
- **Accumulate / Wait for dip / Avoid** - how many stocks got each call in the sector scan.
- **Best liquid Accumulate picks** - the top Accumulate calls (name and Buy Score) that trade at least ₹10 cr a day, so you can actually build a position. A dash means no liquid stock qualifies right now.
- A strong sector with few Accumulate calls usually means most stocks are near their highs - good for momentum, but wait for dips rather than chasing.
        """
    )
