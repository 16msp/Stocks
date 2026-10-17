"""Streamlit page: filter every stock in every sector on any indicator shown on the cards."""

import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import streamlit as st

sys.path.append(str(Path(__file__).resolve().parent.parent))
from strategies import favourites  # noqa: E402
from strategies import position_guide as pg  # noqa: E402
from strategies import price_store  # noqa: E402
from strategies import sector_scanner as scanner  # noqa: E402
from strategies.config import get_setting  # noqa: E402
from strategies.holdings_style import style_holdings  # noqa: E402
from strategies.scan_all import scan_all_sectors  # noqa: E402
from strategies.scan_ui import favourite_symbols, render_card, screener_url, tradingview_url  # noqa: E402

st.set_page_config(page_title="Stock Screener", page_icon="🎛️", layout="wide")

from strategies.auth_gate import require_password  # noqa: E402

require_password()
st.title("🎛️ Stock Screener")
st.caption(
    "Filter every stock in every sector on any indicator from the cards - bounce off the low, drop from the high, "
    "relative strength, volume, supply and more. Slide a range to its end to leave that side open. Uses the stored "
    "prices only. Technicals only - check fundamentals before buying. Not investment advice."
)

DEFAULT_SHEET = get_setting("POSITIONS_SHEET_URL", "")

if "screener_nonce" not in st.session_state:
    st.session_state.screener_nonce = 0

st.sidebar.header("Screener")
if st.sidebar.button("🔄 Recalculate", width="stretch"):
    st.session_state.screener_nonce += 1
    scan_all_sectors.clear()


@st.cache_data(ttl=300, show_spinner="Checking which of these you own...")
def _cached_owned(source: str):
    return set(pg.load_open_positions(source)["Stock"].dropna().unique())


full, _summaries = scan_all_sectors(st.session_state.screener_nonce)
try:
    owned = _cached_owned(DEFAULT_SHEET) if DEFAULT_SHEET else set()
except Exception:
    owned = set()

# One row per stock (RAILTEL is in two sectors): join the sector names.
base = full.drop(columns=["Sector", "Owned", "Pick"]).drop_duplicates("Stock").set_index("Stock")
sector_of = full.groupby("Stock")["Sector"].agg(lambda s: ", ".join(dict.fromkeys(s)))
data = base.join(sector_of).reset_index()
data["Owned"] = data["Stock"].map(lambda s: "✅ Owned" if s in owned else "")
fav_now = favourite_symbols()

# ---- numeric filters: (column, label, scale to display units, step, number format, hard low, hard high) ----
NUMERIC = {
    "Strength and timing": [
        ("Buy Score", "Buy Score", 1, 1, "%.0f", 0, 100),
        ("Avg Value 20D", "Avg traded value (₹ cr/day)", 1, 1, "%.0f", None, None),
        ("CMP", "CMP (₹)", 1, 1, "%.0f", None, None),
    ],
    "Price vs high and low": [
        ("52W High Drop", "Drop from 52W high (%)", 100, 1, "%.0f%%", None, 0),
        ("Off 52W Low", "Bounce off 52W low (%)", 100, 1, "%.0f%%", 0, None),
        ("Days Since Low", "Days since 52W low (sessions)", 1, 1, "%.0f", 0, None),
        ("RSI 14", "RSI 14", 1, 1, "%.0f", 0, 100),
    ],
    "Relative strength and trend": [
        ("RS 1M", "RS 1M vs Nifty (pts)", 100, 1, "%.0f%%", None, None),
        ("RS 3M", "RS 3M vs Nifty (pts)", 100, 1, "%.0f%%", None, None),
        ("vs 200DMA", "vs 200DMA (%)", 100, 1, "%.0f%%", None, None),
    ],
    "Volume": [
        ("Up/Down Vol", "Up/Down volume (x)", 1, 0.1, "%.1fx", 0, None),
        ("Vol Trend", "Volume trend 20d/90d (x)", 1, 0.1, "%.1fx", 0, None),
    ],
    "Supply, profile and volatility": [
        ("Overhead Supply", "Overhead supply (%)", 100, 1, "%.0f%%", 0, 100),
        ("Supply to +25%", "Supply to +25% (%)", 100, 1, "%.0f%%", 0, 100),
        ("vs POC", "CMP vs volume POC (%)", 100, 1, "%.0f%%", None, None),
        ("Volatility Ratio", "Volatility ratio (x)", 1, 0.05, "%.2fx", 0, None),
    ],
}


def _bounds(col, scale, step, hard_lo, hard_hi):
    s = data[col].dropna() * scale
    lo = hard_lo if hard_lo is not None else math.floor(s.quantile(0.01) / step) * step
    hi = hard_hi if hard_hi is not None else math.ceil(s.quantile(0.99) / step) * step
    if lo >= hi:
        hi = lo + step
    return float(lo), float(hi)


BOUNDS = {spec[0]: _bounds(*[spec[0], spec[2], spec[3], spec[5], spec[6]]) for group in NUMERIC.values() for spec in group}
ALL_RECS = ["Accumulate", "Strong - wait for dip", "Watch - base forming", "Avoid for now", "Price break - check", "Insufficient data"]
ALL_ZONES = ["Below value area", "At POC", "In value area", "Above value area"]


def _reset():
    for col, (lo, hi) in BOUNDS.items():
        st.session_state[f"f_{col}"] = (lo, hi)
    st.session_state["f_sector"] = []
    st.session_state["f_rec"] = []
    st.session_state["f_zone"] = []
    st.session_state["f_owned"] = "All"
    st.session_state["f_fav"] = "All"
    st.session_state["f_search"] = ""


def _preset(**ranges):
    """ranges: column -> (low or None, high or None) in display units; None = open end."""
    def apply():
        _reset()
        for col, (lo, hi) in ranges.items():
            blo, bhi = BOUNDS[col]
            lo = blo if lo is None else min(max(lo, blo), bhi)
            hi = bhi if hi is None else min(max(hi, blo), bhi)
            st.session_state[f"f_{col}"] = (lo, hi)
    return apply


if "f_search" not in st.session_state:  # first visit: seed every filter through session_state (widgets then take no default)
    _reset()

st.sidebar.subheader("Quick setups")
st.sidebar.button("🧱 Base-building", on_click=_preset(**{"52W High Drop": (None, -20), "Days Since Low": (20, None),
                  "Up/Down Vol": (1.2, None), "Avg Value 20D": (5, None)}), width="stretch",
                  help="20%+ below the high, no fresh low for 20+ sessions, buyers dominating volume, ₹5 cr+/day")
st.sidebar.button("↗️ Early bounce", on_click=_preset(**{"Off 52W Low": (5, 40), "Days Since Low": (10, 60),
                  "Up/Down Vol": (1.2, None), "RS 1M": (0, None), "Avg Value 20D": (5, None)}), width="stretch",
                  help="5-40% off the low, low made 10-60 sessions ago, buyers dominating, beating Nifty over 1M")
st.sidebar.button("💪 Liquid and strong", on_click=_preset(**{"Buy Score": (65, None), "Avg Value 20D": (20, None)}),
                  width="stretch", help="Buy Score 65+ and ₹20 cr+/day")
st.sidebar.button("🧹 Clear all filters", on_click=_reset, width="stretch")

with st.sidebar.expander("Sector, call and watchlists", expanded=True):
    st.text_input("Search symbol or company", key="f_search", placeholder="e.g. HDFC or bank")
    st.multiselect("Sector", sorted(set(", ".join(data["Sector"]).split(", "))), key="f_sector", placeholder="All sectors")
    st.multiselect("Recommendation", ALL_RECS, key="f_rec", placeholder="All calls")
    st.multiselect("Volume-profile zone", ALL_ZONES, key="f_zone", placeholder="Any zone")
    st.selectbox("Ownership", ["All", "Owned only", "Not owned"], key="f_owned")
    st.selectbox("Favourites", ["All", "Favourites only", "Not favourites"], key="f_fav")

for group, specs in NUMERIC.items():
    with st.sidebar.expander(group, expanded=group in ("Strength and timing", "Price vs high and low")):
        for col, label, _scale, step, fmt, _lo, _hi in specs:
            lo, hi = BOUNDS[col]
            st.slider(label, lo, hi, step=float(step), format=fmt, key=f"f_{col}",
                      help="Slide to either end to leave that side open. Stocks with no value for this indicator are hidden while it is active.")

# ---- apply filters ----
mask = pd.Series(True, index=data.index)
active = []
for group, specs in NUMERIC.items():
    for col, label, scale, _step, _fmt, _lo, _hi in specs:
        blo, bhi = BOUNDS[col]
        lo, hi = st.session_state[f"f_{col}"]
        vals = data[col] * scale
        if lo > blo:
            mask &= vals >= lo - 1e-9
            active.append(f"{label} ≥ {lo:g}")
        if hi < bhi:
            mask &= vals <= hi + 1e-9
            active.append(f"{label} ≤ {hi:g}")
q = st.session_state["f_search"].strip().lower()
if q:
    mask &= data["Stock"].str.lower().str.contains(q, regex=False) | data["Company"].fillna("").str.lower().str.contains(q, regex=False)
    active.append(f"search '{q}'")
if st.session_state["f_sector"]:
    wanted = set(st.session_state["f_sector"])
    mask &= data["Sector"].map(lambda s: bool(wanted & set(s.split(", "))))
    active.append("sector: " + ", ".join(sorted(wanted)))
if st.session_state["f_rec"]:
    mask &= data["Recommendation"].isin(st.session_state["f_rec"])
    active.append("call: " + ", ".join(st.session_state["f_rec"]))
if st.session_state["f_zone"]:
    mask &= data["VP Zone"].isin(st.session_state["f_zone"])
    active.append("zone: " + ", ".join(st.session_state["f_zone"]))
if st.session_state["f_owned"] == "Owned only":
    mask &= data["Owned"] != ""
    active.append("owned only")
elif st.session_state["f_owned"] == "Not owned":
    mask &= data["Owned"] == ""
    active.append("not owned")
if st.session_state["f_fav"] == "Favourites only":
    mask &= data["Stock"].isin(fav_now)
    active.append("favourites only")
elif st.session_state["f_fav"] == "Not favourites":
    mask &= ~data["Stock"].isin(fav_now)
    active.append("not favourites")

res = data[mask].sort_values("Buy Score", ascending=False, na_position="last").reset_index(drop=True)

store = price_store.get_status(list(data["Stock"]))
m1, m2, m3, m4 = st.columns(4)
m1.metric("Stocks matching", f"{len(res)} of {len(data)}")
m2.metric("🟢 Accumulate", int((res["Recommendation"] == "Accumulate").sum()))
m3.metric("🟡 Wait for dip / Watch", int(res["Recommendation"].isin(["Strong - wait for dip", "Watch - base forming"]).sum()))
m4.metric("🔴 Avoid", int((res["Recommendation"] == "Avoid for now").sum()))
if active:
    st.caption("Filters: " + " · ".join(active))
if store:
    st.caption(f"💾 Prices stored in {store['backend']}, latest session {store['latest_session']}.")

if res.empty:
    st.info("No stock matches all of these filters. Loosen a range or press **Clear all filters**.")
    st.stop()

table_tab, cards_tab = st.tabs(["📊 Table", "🃏 Cards (top 30 by Buy Score)"])

with table_tab:
    cols = ["Stock", "Sector", "Recommendation", "Buy Score", "CMP", "52W High Drop", "Off 52W Low", "Days Since Low", "RSI 14",
            "RS 1M", "RS 3M", "vs 200DMA", "Vol Trend", "Up/Down Vol", "Overhead Supply", "Supply to +25%",
            "Volatility Ratio", "vs POC", "VP Zone", "Avg Value 20D", "Owned", "Why"]
    view = res[[c for c in cols if c in res.columns]].copy()
    view.insert(1, "TradingView", view["Stock"].map(tradingview_url))
    view.insert(2, "Screener", view["Stock"].map(screener_url))
    view.insert(0, "❤️", view["Stock"].isin(fav_now))

    st.caption("Sorted by Buy Score (click a header to re-sort, or use the table's search icon). Tick ❤️ on the stocks you want to analyse "
               "further, then press **Save favourites** - they appear on the Favourites page.")
    edited = st.data_editor(
        style_holdings(view),
        column_config={
            "❤️": st.column_config.CheckboxColumn("❤️", help="Tick to add to favourites", width="small"),
            "TradingView": st.column_config.LinkColumn("TradingView", display_text="📈 Chart"),
            "Screener": st.column_config.LinkColumn("Screener", display_text="📊 Screener"),
            "Buy Score": st.column_config.NumberColumn(format="%.0f"),
            "CMP": st.column_config.NumberColumn(format="%.2f"),
            "52W High Drop": st.column_config.NumberColumn(format="percent"),
            "Off 52W Low": st.column_config.NumberColumn(format="percent", help="Bounce off the 52-week low"),
            "Days Since Low": st.column_config.NumberColumn(format="%.0f"),
            "RSI 14": st.column_config.NumberColumn(format="%.0f"),
            "RS 1M": st.column_config.NumberColumn(format="percent"),
            "RS 3M": st.column_config.NumberColumn(format="percent"),
            "vs 200DMA": st.column_config.NumberColumn(format="percent"),
            "Vol Trend": st.column_config.NumberColumn(format="%.2fx"),
            "Up/Down Vol": st.column_config.NumberColumn(format="%.2fx"),
            "Overhead Supply": st.column_config.NumberColumn(format="percent"),
            "Supply to +25%": st.column_config.NumberColumn(format="percent"),
            "Volatility Ratio": st.column_config.NumberColumn(format="%.2fx"),
            "vs POC": st.column_config.NumberColumn(format="percent"),
            "Avg Value 20D": st.column_config.NumberColumn(format="₹%.1f cr"),
            "Why": st.column_config.TextColumn(width="large"),
        },
        disabled=[c for c in view.columns if c != "❤️"],
        hide_index=True,
        width="stretch",
        height=min(35 * (len(view) + 1) + 3, 700),
        key=f"screener_editor_{abs(hash(tuple(view['Stock']))) % 10**8}",  # new key per result set so ticks never land on other rows
    )

    if st.button("💾 Save favourites", type="primary"):
        want = set(edited.loc[edited["❤️"], "Stock"])
        shown = set(view["Stock"])
        to_add = sorted(want - fav_now)
        to_remove = sorted((shown & fav_now) - want)
        sector_map = dict(zip(res["Stock"], res["Sector"]))
        try:
            for sym in to_add:
                favourites.add(sym, sector_map.get(sym, "").split(", ")[0])
            favourites.remove(to_remove)
            favourite_symbols(refresh=True)
            st.session_state["screener_flash"] = f"Added {len(to_add)} favourite(s), removed {len(to_remove)}."
            st.rerun()
        except Exception as exc:
            st.error(f"Could not save: {exc}")

    st.download_button(
        "⬇️ Download these results as CSV",
        data=view.drop(columns=["❤️", "TradingView", "Screener"]).to_csv(index=False).encode("utf-8"),
        file_name="screener_results.csv",
        mime="text/csv",
    )

with cards_tab:
    if len(res) > 30:
        st.caption(f"Showing the top 30 of {len(res)} matches; narrow the filters or use the Table tab for the rest.")
    for _, row in res.head(30).iterrows():
        card = row.copy()
        card["Pick"] = ""
        render_card(card, str(row["Sector"]).split(", ")[0], key_prefix="scrcard")

if st.session_state.get("screener_flash"):
    st.toast(st.session_state.pop("screener_flash"), icon="❤️")
