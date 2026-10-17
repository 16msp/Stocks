"""Streamlit page: stocks that fit Mark Minervini's Trend Template (Stage 2 uptrend), from every sector list."""

import sys
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.append(str(Path(__file__).resolve().parent.parent))
from strategies import entry_plan  # noqa: E402
from strategies import price_store  # noqa: E402
from strategies import sector_scanner as scanner  # noqa: E402
from strategies import trend_template as tt  # noqa: E402
from strategies.scan_all import scan_all_sectors  # noqa: E402
from strategies.scan_ui import render_card, screener_url, tradingview_url  # noqa: E402

st.set_page_config(page_title="Trend Template", page_icon="📐", layout="wide")

from strategies.auth_gate import require_password  # noqa: E402

require_password()
st.title("📐 Minervini Trend Template")
st.caption(
    "Stocks from every sector list that pass Mark Minervini's eight Stage 2 uptrend conditions (price above the rising 150 & 200-day averages, "
    "50-day above both, 30%+ above the 52-week low, within 25% of the 52-week high, strong relative strength). Uses closing prices from the "
    "stored data only. Technicals only - check fundamentals before buying. Not investment advice."
)

if "trend_nonce" not in st.session_state:
    st.session_state.trend_nonce = 0
st.sidebar.header("Trend Template")
if st.sidebar.button("🔄 Recalculate", width="stretch"):
    st.session_state.trend_nonce += 1
    scan_all_sectors.clear()

@st.cache_data(ttl=3600, show_spinner="Working out buy points and stops...")
def _plans(symbols: tuple, nonce: int) -> pd.DataFrame:
    hist = price_store.load_history(list(symbols))
    rows = [{"Stock": s, **(entry_plan.plan(hist[s]) if s in hist else entry_plan.EMPTY)} for s in symbols]
    return pd.DataFrame(rows).set_index("Stock")


STATUS_ORDER = {"Broke out": 0, "Near pivot": 1, "Building": 2, "Extended": 3, "No data": 4}

full, _summaries = scan_all_sectors(st.session_state.trend_nonce)
if "SMA 200" not in full.columns:
    st.error("The stored scan has no moving-average columns yet. Press **Recalculate** in the sidebar.")
    st.stop()

# One row per stock (a stock in two sectors appears once, with both sector names).
base = full.drop(columns=["Sector", "Owned", "Pick"]).drop_duplicates("Stock").set_index("Stock")
sector_of = full.groupby("Stock")["Sector"].agg(lambda s: ", ".join(dict.fromkeys(s)))
data = base.join(sector_of).reset_index()

with st.expander("Thresholds (Minervini's own are the defaults)"):
    c1, c2, c3 = st.columns(3)
    near_high = c1.slider("Within this % of the 52-week high", 5, 40, int(tt.DEFAULTS["near_high"] * 100), step=1) / 100
    above_low = c2.slider("At least this % above the 52-week low", 10, 100, int(tt.DEFAULTS["above_low"] * 100), step=5) / 100
    min_rs = c3.slider("Minimum RS rating (1-99)", 1, 99, tt.DEFAULTS["min_rs"], step=1)
    st.caption("The RS rating ranks each stock's blended 3/6/9/12-month return against the other stocks in these sector lists (99 = strongest).")

evaluated = tt.evaluate(data, near_high=near_high, above_low=above_low, min_rs=min_rs)
judged = evaluated[evaluated["Criteria met"].notna()].copy()
no_history = len(evaluated) - len(judged)

f1, f2, f3, f4, f5 = st.columns([1.1, 1.8, 1.2, 1.3, 1.6])
need = f1.selectbox("Criteria to meet", [8, 7, 6, 5], format_func=lambda n: f"All 8" if n == 8 else f"At least {n} of 8", index=0)
sectors = f2.multiselect("Sectors", sorted(scanner.SECTORS), placeholder="All sectors")
hide_thin = f3.checkbox(f"Hide thinly traded (< ₹{scanner.MIN_LIQUIDITY_CR} cr/day)", value=True)
tight_only = f4.checkbox("Only volatility-contracting (VCP-style)", value=False, help="Average true range over the last 20 sessions is below 85% of its 100-session average. This checks the tightening of daily ranges only; read the Trade plan on each card for the actual base.")
setup = f5.multiselect("Setup status", ["Broke out", "Near pivot", "Building", "Extended"], placeholder="Any status", help="Broke out: through the pivot, within 5%. Near pivot: within 3% below it. Building: further below. Extended: more than 5% above the pivot.")

res = judged[judged["Criteria met"] >= need]
res = res[res["Recommendation"] != "Price break - check"]
if hide_thin:
    res = res[res["Avg Value 20D"].fillna(0) >= scanner.MIN_LIQUIDITY_CR]
if sectors:
    res = res[res["Sector"].map(lambda s: any(x in sectors for x in s.split(", ")))]
if tight_only:
    res = res[res["Volatility Ratio"] < 0.85]
plans = _plans(tuple(sorted(res["Stock"])), st.session_state.trend_nonce) if len(res) else pd.DataFrame(columns=entry_plan.COLUMNS)
res = res.join(plans, on="Stock")
if setup:
    res = res[res["Status"].isin(setup)]
res = res.assign(_s=res["Status"].map(STATUS_ORDER)).sort_values(["_s", "Criteria met", "RS Rating"], ascending=[True, False, False]).drop(columns="_s")
res["Template why"] = res.apply(
    lambda r: "✔ " + "; ".join(tt.CRITERIA[c] for c in tt.CRITERIA if r[c]) + (" | ✖ " + r["Missing"].replace(", ", "; ") if r["Missing"] else ""), axis=1,
) if len(res) else pd.Series(dtype=str)

m1, m2, m3, m4 = st.columns(4)
m1.metric("Stocks judged", len(judged), help="Needs about 10 months of stored prices to work out the 200-day average and its 1-month trend.")
m2.metric("Pass all 8", int((judged["Criteria met"] == 8).sum()))
m3.metric("Pass 7 or more", int((judged["Criteria met"] >= 7).sum()))
m4.metric("Shown now", len(res))
if no_history:
    st.caption(f"{no_history} stock(s) were left out because they have too little price history to judge.")

with st.expander("How many stocks pass each condition?"):
    breadth = pd.DataFrame({"Condition": [tt.CRITERIA[c] for c in tt.CRITERIA], "Stocks passing": [int(judged[c].sum()) for c in tt.CRITERIA]})
    breadth["Share of stocks"] = breadth["Stocks passing"] / max(len(judged), 1)
    st.dataframe(breadth, column_config={"Share of stocks": st.column_config.NumberColumn(format="percent")}, hide_index=True, width="stretch")
    st.caption("The condition with the lowest pass rate is the one holding most stocks back. In a weak market that is usually 'within 25% of the 52-week high'.")

table_tab, cards_tab = st.tabs(["📊 Table", "🃏 Cards (top 30 by RS rating)"])

with table_tab:
    if res.empty:
        st.info("No stocks fit with these settings. Try 'At least 7 of 8', which also lists the one condition each is missing.")
    else:
        cols = ["Stock", "Sector", "Status", "Verdict", "CMP", "Pivot", "Entry", "Buy up to", "Stop", "Risk %", "Target 2R", "Target 3R", "Contractions",
                "Tightening", "Volume dried up", "Volume to confirm", "Criteria met", "Missing", "RS Rating", "SMA 50", "SMA 150", "SMA 200", "200DMA up 4M",
                "52W High Drop", "Off 52W Low", "vs 200DMA", "Volatility Ratio", "Vol Trend", "Up/Down Vol", "Avg Value 20D"]
        view = res[cols].copy()
        view.insert(1, "TradingView", view["Stock"].map(tradingview_url))
        view.insert(2, "Screener", view["Stock"].map(screener_url))
        st.caption("Sorted by setup status (broke out first), then conditions met and RS rating. Click a header to re-sort, or use the table's search icon. "
                   "Entry, stop and targets follow the rules explained under 'How the trade plan is worked out' below.")
        st.dataframe(
            view,
            column_config={
                "TradingView": st.column_config.LinkColumn("TradingView", display_text="📈 Chart"),
                "Screener": st.column_config.LinkColumn("Screener", display_text="📊 Screener"),
                "Verdict": st.column_config.TextColumn(width="large"),
                "Pivot": st.column_config.NumberColumn(format="%.2f", help="The buy point: the high the stock has to trade through"),
                "Entry": st.column_config.NumberColumn(format="%.2f"),
                "Buy up to": st.column_config.NumberColumn(format="%.2f", help="Pivot + 5%. Above this the move is extended: don't chase."),
                "Stop": st.column_config.NumberColumn(format="%.2f", help="Under the 15-session low, never more than 8% below the entry"),
                "Risk %": st.column_config.NumberColumn(format="percent", help="Entry to stop"),
                "Target 2R": st.column_config.NumberColumn(format="%.2f", help="Entry + 2x the risk"),
                "Target 3R": st.column_config.NumberColumn(format="%.2f", help="Entry + 3x the risk"),
                "Tightening": st.column_config.CheckboxColumn(help="The last pullback is under 8% and well under the biggest one"),
                "Volume dried up": st.column_config.CheckboxColumn(help="Average volume of the last 10 sessions is below 80% of the 50-day average"),
                "Volume to confirm": st.column_config.NumberColumn(format="%.0f", help="1.4x the 50-day average volume: the breakout day should trade at least this many shares"),
                "Criteria met": st.column_config.NumberColumn(format="%.0f / 8"),
                "Missing": st.column_config.TextColumn(width="medium"),
                "RS Rating": st.column_config.NumberColumn(format="%.0f", help="1-99 against the stocks in these sector lists"),
                "CMP": st.column_config.NumberColumn(format="%.2f"),
                "SMA 50": st.column_config.NumberColumn(format="%.2f"),
                "SMA 150": st.column_config.NumberColumn(format="%.2f"),
                "SMA 200": st.column_config.NumberColumn(format="%.2f"),
                "200DMA up 4M": st.column_config.CheckboxColumn(help="The 200-day average is higher than it was 4 months ago (Minervini prefers 4-5 months)"),
                "52W High Drop": st.column_config.NumberColumn(format="percent"),
                "Off 52W Low": st.column_config.NumberColumn(format="percent", help="Bounce off the 52-week low"),
                "vs 200DMA": st.column_config.NumberColumn(format="percent"),
                "Volatility Ratio": st.column_config.NumberColumn(format="%.2fx", help="Below 0.85 = price range is contracting"),
                "Vol Trend": st.column_config.NumberColumn(format="%.2fx"),
                "Up/Down Vol": st.column_config.NumberColumn(format="%.2fx"),
                "Avg Value 20D": st.column_config.NumberColumn(format="₹%.1f cr"),
                "Buy Score": st.column_config.NumberColumn(format="%.0f"),
            },
            hide_index=True,
            width="stretch",
            height=min(35 * (len(view) + 1) + 3, 700),
        )
        st.download_button(
            "⬇️ Download these results as CSV",
            data=view.drop(columns=["TradingView", "Screener"]).to_csv(index=False).encode("utf-8"),
            file_name="trend_template.csv",
            mime="text/csv",
        )

with cards_tab:
    if len(res) > 30:
        st.caption(f"Showing the top 30 of {len(res)} matches; narrow the filters or use the Table tab for the rest.")
    for _, row in res.head(30).iterrows():
        card = row.copy()
        card["Pick"] = ""
        card["Owned"] = ""
        render_card(card, str(row["Sector"]).split(", ")[0], key_prefix="ttcard", plan=row[entry_plan.COLUMNS].to_dict())

with st.expander("How the trade plan is worked out"):
    st.markdown(
        f"""
- **Pivot (buy point):** the highest high of the last {entry_plan.BASE_SESSIONS} sessions (about 13 weeks). If the stock has already closed through the earlier highs in the last
  {entry_plan.BREAKOUT_LOOKBACK} sessions, the pivot is the high it broke through.
- **Entry:** just above the pivot (+0.1%); for a stock that has broken out already, today's close while it is within 5% of the pivot.
- **Buy up to:** pivot + {entry_plan.CHASE_LIMIT:.0%}. Beyond that the stock is *extended*: Minervini says wait for a new base.
- **Stop:** under the lowest low of the {entry_plan.STOP_LOOKBACK} sessions before the breakout (or before today if it has not broken out), and never more than {entry_plan.MAX_RISK:.0%} below the entry.
  His rule is to cut losses at 7-8%, so if the chart's own low is further away the stop is capped and the setup is shown with an 8% risk.
- **Targets:** entry + 2 or 3 times the risk. A breakout that does not run at least that far is a poor trade; many winners are held much longer.
- **Volume:** the base should show volume drying up, and the breakout day should trade at least {entry_plan.BREAKOUT_VOL_X}x the 50-day average ("Volume to confirm").
- **Tightening:** the last pullback of {entry_plan.MIN_SWING:.0%}+ is under 8% and no more than 60% of the biggest pullback in the base.
- **Not covered:** fundamentals, earnings dates, the market's trend and position size. Check the earnings date before entering. This is a mechanical reading of the chart, not a forecast or advice.
"""
    )
