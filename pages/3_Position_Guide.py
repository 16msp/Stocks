"""Streamlit page: exit/add guide for open positions from Sai's trades workbook."""

import sys
from pathlib import Path

import streamlit as st

sys.path.append(str(Path(__file__).resolve().parent.parent))
from strategies import position_guide as strategy  # noqa: E402

st.set_page_config(page_title="Position Guide", page_icon="🧭", layout="wide")

st.title("🧭 Position Guide")
st.caption(
    "Reads the 'Open Positions' tab of your trades workbook (one row per buy lot) and flags "
    "each tranche/stock as Hold, Add More, or Exit based on your sizing and exit rules, using "
    "live market prices. Sizing (5% cap, 3 tranches, 17% averaging-down) is something you "
    "execute yourself - this just shows where each name stands against it. Not investment advice."
)

DEFAULT_SOURCE = "https://docs.google.com/spreadsheets/d/1pl4T2Avhm4Ks39rQS3ocIkpeQ2RCyTeIzaCP_q9jyWw/edit?usp=sharing"

ADD_STATUS_COLORS = {
    "Ready": "background-color: #c6efce; color: #006100",
    "Nearing": "background-color: #ffeb9c; color: #9c6500",
    "Blocked": "background-color: #ffc7ce; color: #9c0006",
}


def _color_by_add_status(row):
    style = ADD_STATUS_COLORS.get(row["Add Status"], "")
    return [style] * len(row)


@st.cache_data(ttl=300, show_spinner="Fetching live prices from Yahoo Finance...")
def _cached_live_prices(symbols: tuple) -> dict:
    return strategy.fetch_live_prices(list(symbols))


@st.cache_data(ttl=300, show_spinner="Loading positions from Google Sheet...")
def _cached_positions(source_key: str):
    return strategy.load_open_positions(source_key)


st.sidebar.header("Workbook")
path_input = st.sidebar.text_input("Google Sheet URL or local .xlsx path", value=DEFAULT_SOURCE)
uploaded = st.sidebar.file_uploader("...or upload a copy instead", type=["xlsx"])

st.sidebar.header("Live prices")
use_live = st.sidebar.checkbox("Use live CMP (Yahoo Finance, NSE)", value=False)

st.sidebar.header("Sizing rules")
max_alloc_pct = st.sidebar.slider("Max allocation per stock (%)", 1, 15, 5) / 100
max_tranches = st.sidebar.number_input("Max tranches per stock", min_value=1, max_value=15, value=7)
min_gap_pct = st.sidebar.slider("Min gap between CMP & any buy price (%)", 5, 40, 15) / 100
add_drop_pct = st.sidebar.slider("Add on drop from any buy (%)", 5, 40, 17) / 100
if add_drop_pct < min_gap_pct:
    st.sidebar.warning("Add-on-drop is below the min gap - Nearing/Ready bands will overlap oddly.")

st.sidebar.header("Exit rules")
short_term_years = st.sidebar.slider("Short-term cutoff (years)", 0.25, 2.0, 1.0, step=0.25)
short_term_gain = st.sidebar.slider("Short-term exit gain (%)", 5, 50, 25) / 100
mid_term_years = st.sidebar.slider("Mid-term cutoff (years)", short_term_years, 5.0, 2.0, step=0.25)
mid_term_cagr = st.sidebar.slider("Mid-term exit CAGR (%)", 5, 60, 20) / 100
long_term_cagr = st.sidebar.slider("Long-term exit CAGR (%)", 5, 40, 15) / 100

st.sidebar.header("Capital base")
capital_override = st.sidebar.number_input(
    "Capital base for allocation %% (0 = use sum of buy value across all stocks)", min_value=0, value=0, step=50000
)

refresh_clicked = st.sidebar.button("🔄 Refresh (re-fetch sheet + live prices)", type="primary", width="stretch")
if refresh_clicked:
    _cached_live_prices.clear()
    _cached_positions.clear()

try:
    raw = strategy.load_open_positions(uploaded) if uploaded is not None else _cached_positions(path_input)
except Exception as exc:
    st.warning(f"Could not read '{path_input}': {exc}")
    st.stop()

if uploaded is None and strategy.is_google_sheet_url(path_input):
    st.caption(
        "Reading live from Google Sheets (cached 5 min, or hit Refresh) - note Buy Price reflects the sheet's "
        "displayed rounding (2 decimals), not the workbook's full precision."
    )

live_prices = _cached_live_prices(tuple(sorted(raw["Stock"].dropna().unique()))) if use_live and not raw.empty else {}

exit_rules = strategy.ExitRules(
    short_term_years=short_term_years, short_term_gain=short_term_gain,
    mid_term_years=mid_term_years, mid_term_cagr=mid_term_cagr, long_term_cagr=long_term_cagr,
)
sizing_rules = strategy.SizingRules(
    max_allocation_pct=max_alloc_pct, max_tranches=int(max_tranches), add_on_drop_pct=add_drop_pct,
    min_price_gap_pct=min_gap_pct,
)

result: strategy.PositionGuideResult = strategy.analyze(
    raw, exit_rules, sizing_rules, capital_base=capital_override or None, live_prices=live_prices,
)

if not result.ok:
    st.warning(result.message)
    st.stop()

st.success(result.message)
if use_live:
    if result.fallback_symbols:
        st.caption(
            f"Live CMP fetched for {result.live_price_count} stock(s). "
            f"Fell back to workbook CMP for: {', '.join(result.fallback_symbols)}."
        )
    else:
        st.caption(f"Live CMP fetched for all {result.live_price_count} stocks.")
else:
    st.caption("Using workbook CMP (live prices disabled).")

stocks = result.stocks
lots = result.lots
exits = result.exits
add_candidates = result.add_candidates

c1, c2, c3, c4 = st.columns(4)
c1.metric("Capital base used", f"₹{result.capital_base:,.0f}")
c2.metric("Deployed (Buy Value)", f"₹{stocks['Buy Value'].sum():,.0f}")
c3.metric("Current value", f"₹{stocks['Current Value'].sum():,.0f}")
overall_gain = stocks["Current Value"].sum() / stocks["Buy Value"].sum() - 1.0
c4.metric("Unrealized gain", f"{overall_gain:+.1%}")

st.divider()
st.header(f"🔴 Exit — {len(exits)} tranche(s)")
st.caption("Every open buy lot that has cleared its exit bar (short-term flat gain, or CAGR bar by holding period), with the exact tranche to sell.")

if exits.empty:
    st.write("No tranches meet the exit bar right now.")
else:
    st.dataframe(
        exits,
        column_config={
            "Buy Date": st.column_config.DateColumn(format="YYYY-MM-DD"),
            "Buy Price": st.column_config.NumberColumn(format="%.2f"),
            "CMP": st.column_config.NumberColumn(format="%.2f"),
            "Gain %": st.column_config.NumberColumn(format="percent"),
            "Current Value": st.column_config.NumberColumn(format="₹%.0f"),
            "CAGR": st.column_config.NumberColumn(format="percent"),
            "Holding Years": st.column_config.NumberColumn(format="%.2f"),
        },
        hide_index=True, width="stretch",
    )
    st.download_button(
        "⬇️ Download exit list as CSV",
        data=exits.to_csv(index=False).encode("utf-8"),
        file_name="exit_tranches.csv",
        mime="text/csv",
    )

st.divider()
st.header(f"🟢 Add More — {len(add_candidates)} stock(s) with tranches available")
st.caption(
    f"Stocks with fewer than {int(max_tranches)} tranches bought. Max Buy Allowed = 5% cap - already deployed in "
    f"that stock. 'Down %' is CMP vs. every existing buy price for that stock (worst case, not just the last buy) - "
    f"{add_drop_pct:.0%}+ down from all of them is 'Ready', {min_gap_pct:.0%}-{add_drop_pct:.0%} is 'Nearing', below "
    f"{min_gap_pct:.0%} is 'Blocked' (still clustering on a past buy, or hasn't dropped at all). "
    "'Past Price-Gap Flag' is a separate, informational audit of trades already taken too close together."
)

if add_candidates.empty:
    st.write("No stocks currently have tranches available to add.")
else:
    st.dataframe(
        add_candidates.style.apply(_color_by_add_status, axis=1),
        column_config={
            "Allocation %": st.column_config.NumberColumn(format="percent"),
            "Last Buy Price": st.column_config.NumberColumn(format="%.2f"),
            "CMP": st.column_config.NumberColumn(format="%.2f"),
            "Down %": st.column_config.NumberColumn(format="percent"),
            "Max Buy Allowed": st.column_config.NumberColumn(format="₹%.0f"),
        },
        hide_index=True, width="stretch",
    )
    st.download_button(
        "⬇️ Download add-more candidates as CSV",
        data=add_candidates.to_csv(index=False).encode("utf-8"),
        file_name="add_more_candidates.csv",
        mime="text/csv",
    )
