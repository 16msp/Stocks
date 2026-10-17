"""Streamlit page: every stock you marked with the heart in the Sector Scanner, on one screen."""

import sys
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.append(str(Path(__file__).resolve().parent.parent))
from strategies import favourites  # noqa: E402
from strategies import position_guide as pg  # noqa: E402
from strategies import price_store  # noqa: E402
from strategies import sector_scanner as scanner  # noqa: E402
from strategies.config import get_setting  # noqa: E402
from strategies.holdings_style import style_holdings  # noqa: E402
from strategies.scan_ui import favourite_symbols, render_card, screener_url, tradingview_url  # noqa: E402

st.set_page_config(page_title="Favourites", page_icon="❤️", layout="wide")

from strategies.auth_gate import require_password  # noqa: E402

require_password()
st.title("❤️ Favourites - further analysis")
st.caption(
    "Every stock you marked with the heart on a Sector Scanner card, with live technicals, across all sectors. "
    "Add a note to remember why you picked it, or remove it once you've decided. Saved in the database, so the "
    "list is the same on every device. Technicals only - check fundamentals before buying. Not investment advice."
)

DEFAULT_SHEET = get_setting("POSITIONS_SHEET_URL", "")

st.sidebar.header("Favourites")
if st.sidebar.button("🔄 Reload list + recalculate", width="stretch"):
    favourite_symbols(refresh=True)
    st.cache_data.clear()
st.sidebar.page_link("pages/2_Sector_Overview.py", label="← All sectors overview")
st.sidebar.page_link("pages/3_Sector_Scanner.py", label="🔭 Sector Scanner")

if st.session_state.get("fav_flash"):
    st.success(st.session_state.pop("fav_flash"))

try:
    favs = favourites.load()
except Exception as exc:
    st.error(f"Could not read the favourites list: {exc}")
    st.stop()
favourite_symbols(refresh=True)  # keep the heart state on the cards in sync with what is stored

if favs.empty:
    st.info("No favourites yet. Open the **Sector Scanner**, and click the 🤍 beside a stock's name on its card to add it here.")
    st.stop()


@st.cache_data(ttl=300, show_spinner="Checking which of these you own...")
def _cached_owned(source: str):
    return set(pg.load_open_positions(source)["Stock"].dropna().unique())


@st.cache_data(ttl=900, show_spinner="Calculating technicals for your favourites...")
def _scan(symbols: tuple, sectors: tuple, owned: frozenset):
    """Same scan as the sector pages, for just these stocks (stored prices only, no download)."""
    parts = []
    for sector in sorted(set(sectors)):
        if sector in scanner.SECTORS:
            uni, _ = scanner.load_universe(sector)
            parts.append(uni[uni["Stock"].isin(symbols)])
    universe = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=["Stock", "Company"])
    # A favourite whose sector was renamed or removed still gets scanned, just without a company name.
    known = set(universe["Stock"])
    extra = pd.DataFrame({"Stock": [s for s in symbols if s not in known], "Company": ""})
    universe = pd.concat([universe, extra], ignore_index=True).drop_duplicates("Stock")
    stats = pg.fetch_market_stats(list(universe["Stock"]), update=False)
    return scanner.scan_sector("Favourites", stats, universe, set(owned))


try:
    owned = frozenset(_cached_owned(DEFAULT_SHEET)) if DEFAULT_SHEET else frozenset()
except Exception:
    owned = frozenset()

res = _scan(tuple(sorted(favs["Stock"])), tuple(favs["Sector"]), owned)
if not res.ok:
    st.warning(res.message)
    st.stop()

t = res.table.merge(favs[["Stock", "Sector", "Note", "Added"]], on="Stock", how="left")
t["Pick"] = ""  # the top-3 star is a per-sector idea; not meaningful across sectors

store = price_store.get_status(list(t["Stock"]))
if store:
    st.caption(f"💾 Prices stored in {store['backend']}, latest session {store['latest_session']}.")

m1, m2, m3, m4 = st.columns(4)
m1.metric("Favourites", len(t))
m2.metric("🟢 Accumulate", int((t["Recommendation"] == "Accumulate").sum()))
m3.metric("🟡 Wait for dip / Watch", int(t["Recommendation"].isin(["Strong - wait for dip", "Watch - base forming"]).sum()))
m4.metric("🔴 Avoid", int((t["Recommendation"] == "Avoid for now").sum()))

table_tab, cards_tab = st.tabs(["📊 All on one screen", "🃏 Cards"])

with table_tab:
    cols = ["Stock", "Sector", "Recommendation", "Buy Score", "CMP", "52W High Drop", "ATH Drop", "RS 1M", "RS 3M", "vs 200DMA",
            "Vol Trend", "Up/Down Vol", "Overhead Supply", "Supply to +25%", "RSI 14", "Days Since Low",
            "Volatility Ratio", "Off 52W Low", "vs POC", "VP Zone", "Avg Value 20D"]
    view = t[[c for c in cols if c in t.columns]].copy()
    view.insert(1, "TradingView", view["Stock"].map(tradingview_url))
    view.insert(2, "Screener", view["Stock"].map(screener_url))
    view.insert(view.columns.get_loc("Buy Score") + 1, "Note", t["Note"].fillna("").to_numpy())
    view.insert(view.columns.get_loc("Note") + 1, "Remove", False)
    view = view.sort_values("Buy Score", ascending=False).reset_index(drop=True)

    st.caption("Sorted by Buy Score. Type in the **Note** column (e.g. 'wait for 5% dip', 'check Q3 results'), tick **Remove** to drop a stock, then press Save.")
    edited = st.data_editor(
        style_holdings(view),
        column_config={
            "TradingView": st.column_config.LinkColumn("TradingView", display_text="📈 Chart"),
            "Screener": st.column_config.LinkColumn("Screener", display_text="📊 Screener"),
            "Buy Score": st.column_config.NumberColumn(format="%.0f"),
            "CMP": st.column_config.NumberColumn(format="%.2f"),
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
            "Avg Value 20D": st.column_config.NumberColumn(format="₹%.1f cr"),
            "Note": st.column_config.TextColumn(width="medium", help="Your own reminder - saved when you press Save"),
            "Remove": st.column_config.CheckboxColumn(help="Tick and press Save to remove from favourites"),
        },
        disabled=[c for c in view.columns if c not in ("Note", "Remove")],
        hide_index=True,
        width="stretch",
        height=35 * (len(view) + 1) + 3,
        key="fav_editor",
    )

    if st.button("💾 Save notes and removals", type="primary"):
        old = dict(zip(favs["Stock"], favs["Note"].fillna("")))
        changed = {r.Stock: r.Note for r in edited.itertuples() if (r.Note or "") != old.get(r.Stock, "")}
        drop = [r.Stock for r in edited.itertuples() if r.Remove]
        try:
            favourites.set_notes({k: v for k, v in changed.items() if k not in drop})
            favourites.remove(drop)
            favourite_symbols(refresh=True)
            st.cache_data.clear()
            st.session_state["fav_flash"] = f"Saved {len(changed)} note change(s), removed {len(drop)} stock(s)."
            st.rerun()
        except Exception as exc:
            st.error(f"Could not save: {exc}")

    st.download_button(
        "⬇️ Download favourites as CSV",
        data=view.drop(columns=["Remove", "TradingView", "Screener"]).to_csv(index=False).encode("utf-8"),
        file_name="favourites.csv",
        mime="text/csv",
    )

with cards_tab:
    for _, row in t.sort_values("Buy Score", ascending=False).iterrows():
        if row.get("Note"):
            st.caption(f"📝 {row['Stock']}: {row['Note']}")
        render_card(row, row["Sector"], key_prefix="favcard")
