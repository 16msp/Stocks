"""Streamlit page: exit/add guide for open positions from Sai's trades workbook."""

import sys
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.append(str(Path(__file__).resolve().parent.parent))
from strategies import position_guide as strategy  # noqa: E402
from strategies.config import get_setting  # noqa: E402
from strategies.holdings_style import style_holdings  # noqa: E402

st.set_page_config(page_title="Position Guide", page_icon="🧭", layout="wide")

from strategies.auth_gate import require_password  # noqa: E402

require_password()

st.title("🧭 Position Guide")
st.caption(
    "Reads the 'Open Positions' tab of your trades workbook (one row per buy lot) and flags "
    "each tranche/stock as Hold, Add More, or Exit based on your sizing and exit rules, using "
    "live market prices. Sizing (5% cap, 3 tranches, 17% averaging-down) is something you "
    "execute yourself - this just shows where each name stands against it. Not investment advice."
)

DEFAULT_SOURCE = get_setting("POSITIONS_SHEET_URL", "")  # kept out of the code: set in .streamlit/secrets.toml or env

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


@st.cache_data(ttl=3600, show_spinner="Fetching price history for RS & technicals...")
def _cached_market_stats(symbols: tuple, force: bool):
    return strategy.fetch_market_stats(list(symbols), force=force)


@st.cache_data(ttl=300, show_spinner="Loading positions from Google Sheet...")
def _cached_positions(source_key: str):
    return strategy.load_open_positions(source_key)


st.sidebar.header("Workbook")
path_input = st.sidebar.text_input("Google Sheet URL or local .xlsx path", value=DEFAULT_SOURCE)
uploaded = st.sidebar.file_uploader("...or upload a copy instead", type=["xlsx"])

st.sidebar.header("Live prices")
use_live = st.sidebar.checkbox("Use live CMP (Yahoo Finance, NSE)", value=False)
use_rs = st.sidebar.checkbox("Show RS vs Nifty & technicals", value=True)

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
    _cached_market_stats.clear()

if not path_input and uploaded is None:
    st.info("Add your Open Positions sheet: paste its Google Sheet URL in the sidebar, upload a copy, or set POSITIONS_SHEET_URL in .streamlit/secrets.toml.")
    st.stop()

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
rs_table = _cached_market_stats(tuple(sorted(raw["Stock"].dropna().unique())), bool(refresh_clicked)) if use_rs and not raw.empty else None

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
    market_stats=rs_table,
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

RS_CONFIG = {
    col: st.column_config.NumberColumn(format="percent", help="Stock return minus Nifty 50 return over the window. Positive = beating Nifty.")
    for col in strategy.RS_COLUMNS
}

st.divider()
st.header(f"📋 Holdings — {len(stocks)} stock(s)")
st.caption(
    "One row per stock you own (analysis fields only - sheet details are in the Exit / Add More sections below). "
    "**RS** = stock return minus Nifty return over 1M/3M (positive = beating Nifty). "
    "**52W High Drop** / **ATH Drop** / **vs 200DMA** = CMP vs 52-week closing high / all-time closing high / 200-day average. "
    "**Vol Trend** = 20-day avg volume / 90-day avg (>1x = interest rising). "
    "**Up/Down Vol** = volume on up-days / down-days, last 60 sessions (>1x = buyers dominate). "
    "**Volume profile** is built from the last ~6 months: POC = price with the most volume, VAL-VAH = zone holding 70% of it, "
    "**Buy Score** (0-100, see the guide below) ranks *when* to add. **Days Since Low** = sessions since the 52W low, **Volatility Ratio** = recent vs 100-day average daily range, **RSI 14**, **Off 52W Low** = bounce from the low. **VP Zone** = where CMP sits, **Overhead Supply** = share of that volume traded above CMP (trapped holders who may sell into a recovery), **Supply to +25%** = share of that volume traded between CMP and 25% above it (the supply standing between you and a +25% exit). "
    "**Colours:** 🟩 favours the stock, 🟥 works against it, 🟨 in between - RS beating/lagging Nifty by 2 pts, price above/10% below the 200DMA, "
    "Vol Trend ≥1.2x / <0.7x, Up/Down Vol ≥1.2x / <0.8x, Overhead Supply ≤30% / ≥70%, Supply to +25% ≤40% / ≥70%, "
    "VP Zone green only when volume confirms (below value area with Up/Down ≥1.2x = accumulation; above VAH with rising volume). "
    "Days Since Low ≥25 / <10, Volatility Ratio ≤0.85x / ≥1.15x, Buy Score ≥65 / <40. "
    "52W High Drop, RSI 14, Off 52W Low and vs POC are left uncoloured - they are levels, not good/bad signals. "
    "Based on last completed daily bars; blank = not enough history."
)
with st.expander("Choosing 2-3 from the Add More list: Buy Score"):
    st.markdown(
        """
"Add More" tells you a stock is **cheap enough** (down 17%+ from every buy). It doesn't tell you it has **stopped falling**. With no stop-loss, adding to a stock that is still sliding is the costly mistake, so the **Buy Score (0-100)** ranks *timing*:

| Part | Pts | Full marks when | Why |
|---|---:|---|---|
| **Stabilisation** | 25 | no new 52W low for 30+ sessions (0 at 5) | A base needs time; fresh lows mean sellers are still in charge |
| **Accumulation** | 25 | Up/Down Vol 1.5x+ (0 at 0.8x) | Volume on up-days beating down-days = buyers absorbing supply |
| **Calm** | 15 | Volatility Ratio 0.6x or less (0 at 1.2x) | Tightening range = selling exhausted |
| **Strength** | 15 | RS 3M +10 pts vs Nifty or more (0 at -10) | Leaders recover first |
| **Clear path** | 20 | Supply to +25% 40% or less (0 at 80%) | Little overhead selling between you and your target |

In the Add More table the stocks are sorted Ready → Nearing → Blocked and by Buy Score inside each band, and the top 3 actionable ones (not Blocked) are flagged ⭐. **Rule of thumb:** buy only from the ⭐ names scoring **65+**; if none do, waiting is a valid choice - a cheap stock with a score under 40 is usually a falling knife. RSI 14 and Off 52W Low are context, not scored (RSI under 30 = oversold, but oversold can stay oversold).
        """
    )

with st.expander("How to read the volume profile (what to do with it)"):
    st.markdown(
        """
**Volume profile shows *where* the market agreed on value** - prices with heavy volume act as support/resistance, thin zones get crossed fast.

| Situation | Read it as | Action for you |
|---|---|---|
| **Below value area** + Up/Down Vol > 1x + Vol Trend > 1x | Discount zone with buyers absorbing supply = accumulation | Best place to *add* (if the Add More table says Ready) |
| **Below value area** + Up/Down Vol < 1x | Sellers still in control - cheap for a reason | Do **not** average; wait for Up/Down Vol to turn > 1x |
| **At POC / In value area** | Fair value, balance | Hold; no edge to add or trim |
| **Above VAH** (breaking out) + Vol Trend > 1x | Acceptance at new prices = trend start | Hold winners; this is the confirmation your framework waits for |
| **Above VAH** + Vol Trend < 1x | Stretched on thin volume | Good time to take exits you already planned |

**Overhead Supply** matters most for stocks you're averaging down on: **> 70%** means most of the last 6 months' volume traded *above* today's price - lots of trapped holders, so rallies meet heavy selling (expect slow recovery, set exits at POC/VAH rather than hoping for more). **< 30%** means little resistance above.

**Targets:** use **POC** as the first realistic target and **VAH** as the second, for stocks sitting below the value area.

Limits: built from daily high/low/volume (each day's volume spread evenly over its range), so it's an approximation of a tick-level profile - use it to rank and sanity-check, not for precise levels.
        """
    )

if use_rs and "RS 3M" not in stocks:
    st.warning("Market data (RS / technicals) could not be fetched right now - try Refresh.")

analysis_cols = ["Buy Score"] + strategy.RS_COLUMNS + strategy.TECH_COLUMNS + ["vs POC", "VP Zone"]
vp_hidden = {"VP Low (VAL)", "VP POC", "VP High (VAH)"}
holdings_cols = ["Stock"] + [c for c in analysis_cols if c in stocks.columns and c not in vp_hidden]
st.dataframe(
    style_holdings(stocks[holdings_cols]),
    column_config={
        "52W High Drop": st.column_config.NumberColumn(format="percent"),
        "ATH Drop": st.column_config.NumberColumn(format="percent", help="From the all-time closing high (since listing). 0% = at its all-time high. Yahoo does not adjust for demergers, so a stock that demerged can show a bigger drop than a holder actually lost."),
        "vs 200DMA": st.column_config.NumberColumn(format="percent"),
        "Vol Trend": st.column_config.NumberColumn(format="%.2fx"),
        "Up/Down Vol": st.column_config.NumberColumn(format="%.2fx"),
        "vs POC": st.column_config.NumberColumn(format="percent"),
        "Overhead Supply": st.column_config.NumberColumn(format="percent"),
        "Supply to +25%": st.column_config.NumberColumn(format="percent"),
        "Buy Score": st.column_config.NumberColumn(format="%.0f", help="0-100 timing score: stabilisation 25 + accumulation 25 + calm 15 + strength 15 + clear path 20"),
        "RSI 14": st.column_config.NumberColumn(format="%.0f"),
        "Days Since Low": st.column_config.NumberColumn(format="%.0f", help="Sessions since the 52-week closing low"),
        "Volatility Ratio": st.column_config.NumberColumn(format="%.2fx", help="Avg true range % of last 20 sessions / last 100 (<1 = tightening)"),
        "Off 52W Low": st.column_config.NumberColumn(format="percent"),
        **RS_CONFIG,
    },
    hide_index=True, width="stretch",
)
st.download_button(
    "⬇️ Download holdings as CSV",
    data=stocks[holdings_cols].to_csv(index=False).encode("utf-8"),
    file_name="holdings.csv",
    mime="text/csv",
)

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
            **RS_CONFIG,
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
    add_cols = ["Pick", "Stock", "Add Status", "Buy Score", "Down %", "Up/Down Vol", "Days Since Low",
                "Volatility Ratio", "Supply to +25%", "RS 3M", "RSI 14", "VP Zone"]
    add_cols = [c for c in add_cols if c in add_candidates.columns] + [
        c for c in add_candidates.columns if c not in add_cols and c != "Pick"
    ]
    add_candidates = add_candidates[add_cols]
    st.dataframe(
        add_candidates.style.apply(_color_by_add_status, axis=1),
        column_config={
            "Buy Score": st.column_config.NumberColumn(format="%.0f"),
            "Up/Down Vol": st.column_config.NumberColumn(format="%.2fx"),
            "Days Since Low": st.column_config.NumberColumn(format="%.0f"),
            "Volatility Ratio": st.column_config.NumberColumn(format="%.2fx"),
            "Supply to +25%": st.column_config.NumberColumn(format="percent"),
            "RSI 14": st.column_config.NumberColumn(format="%.0f"),
            "Allocation %": st.column_config.NumberColumn(format="percent"),
            "Last Buy Price": st.column_config.NumberColumn(format="%.2f"),
            "CMP": st.column_config.NumberColumn(format="%.2f"),
            "Down %": st.column_config.NumberColumn(format="percent"),
            "Max Buy Allowed": st.column_config.NumberColumn(format="₹%.0f"),
            **RS_CONFIG,
        },
        hide_index=True, width="stretch",
    )
    st.download_button(
        "⬇️ Download add-more candidates as CSV",
        data=add_candidates.to_csv(index=False).encode("utf-8"),
        file_name="add_more_candidates.csv",
        mime="text/csv",
    )
