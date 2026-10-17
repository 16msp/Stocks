"""Streamlit page: stocks that fit Mark Minervini's Trend Template (Stage 2 uptrend), from every sector list."""

import sys
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.append(str(Path(__file__).resolve().parent.parent))
from strategies import earnings, entry_plan, market_trend  # noqa: E402
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

st.sidebar.subheader("Your position sizing")
capital = st.sidebar.number_input("Capital for these trades (₹)", min_value=10_000, value=100_000, step=10_000,
                                  help="Only the money you would put into this kind of trade. Change it to your own figure.")
risk_pct = st.sidebar.number_input("Loss you accept per trade (% of capital)", min_value=0.25, max_value=5.0, value=1.0, step=0.25,
                                   help="Shares are sized so that being stopped out costs about this much. Minervini risks roughly 1.25-2.5%.")
max_pos_pct = st.sidebar.number_input("Largest position (% of capital)", min_value=5, max_value=100, value=25, step=5,
                                      help="No single stock gets more than this share of your capital, even if the risk limit would allow it.")


@st.cache_data(ttl=3600, show_spinner=False)
def _market() -> dict:
    return market_trend.nifty_trend()


@st.cache_data(ttl=6 * 3600, show_spinner="Looking up results dates (about a second each)...")
def _results_dates(symbols: tuple) -> dict:
    return earnings.next_results(list(symbols))


mkt = _market()
if mkt["state"] == "Uptrend":
    st.success(f"**Market check: Nifty is in an uptrend.** {mkt['detail']}")
elif mkt["state"] == "Mixed":
    st.warning(f"**Market check: Nifty trend is mixed.** Minervini is selective in this kind of market: smaller sizes, only the strongest setups. {mkt['detail']}")
elif mkt["state"] == "Downtrend":
    st.error(f"**Market check: Nifty is in a downtrend.** Minervini does not buy breakouts in a falling market; most of them fail. Treat everything below as a watch list. {mkt['detail']}")
else:
    st.info(f"Market check: {mkt['detail']}")
season = earnings.results_season()
if season:
    st.info(f"**Results season ({season}).** Most listed companies report between now and mid-month. Check each stock's results date before entering: a gap on results can jump past any stop.")

@st.cache_data(ttl=3600, show_spinner="Working out buy points and stops...")
def _plans(symbols: tuple, nonce: int) -> pd.DataFrame:
    hist = price_store.load_history(list(symbols))
    rows = [{"Stock": s, **(entry_plan.plan(hist[s]) if s in hist else entry_plan.EMPTY)} for s in symbols]
    return pd.DataFrame(rows).set_index("Stock")


STATUS_ORDER = {"Broke out": 0, "Retesting": 1, "Near pivot": 2, "Building": 3, "No base": 4, "Extended": 5, "No data": 6}

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
hide_wide = st.checkbox("Hide setups whose chart stop is more than 10% away", value=True,
                        help="Minervini cuts losses at 7-8%. If the base's own low is further away than 10% the setup is a skip, not a trade with a tighter made-up stop.")
strict = st.checkbox(
    "Textbook VCP only: shrinking pullbacks, tight final pullback, volume dried up", value=False,
    help="Needs all three flags from the Trade plan: none of the last three pullbacks is more than 1.5 points deeper than the one before (Shrinking), the last pullback "
         "is under 8% and no more than 60% of the biggest (Tightening), and the last 10 sessions' average volume is below 80% of the 50-day average (Volume dried up). "
         "Combine it with 'Near pivot' or 'Broke out' to find setups that are ready or just triggered.",
)
setup = f5.multiselect("Setup status", ["Broke out", "Retesting", "Near pivot", "Building", "No base", "Extended"], placeholder="Any status", help="Broke out: closed through the pivot recently and still above it, within 5%. Retesting: broke out in the last 10 sessions but is now back under the pivot. Near pivot: within 3% below it. Building: further below. No base: the stock made its high in the last 3 weeks, so there is no base to buy out of. Extended: more than 5% above the pivot.")

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
if hide_wide:
    res = res[~res["Trade check"].astype(str).str.startswith("Skip - stop")]
if strict:
    res = res[res[["Shrinking", "Tightening", "Volume dried up"]].fillna(False).astype(bool).all(axis=1)]
res = res.assign(_s=res["Status"].map(STATUS_ORDER)).sort_values(["_s", "Criteria met", "RS Rating"], ascending=[True, False, False]).drop(columns="_s")
res["Template why"] = res.apply(
    lambda r: "✔ " + "; ".join(tt.CRITERIA[c] for c in tt.CRITERIA if r[c]) + (" | ✖ " + r["Missing"].replace(", ", "; ") if r["Missing"] else ""), axis=1,
) if len(res) else pd.Series(dtype=str)

def _size(row):
    if row["Trade check"] in ("No base yet", "Skip - extended", "No data") or pd.isna(row["Entry"]):
        return pd.Series({"Qty": 0, "Invested": 0.0, "Max loss": 0.0, "Sizing note": "no valid setup to size"})
    if str(row["Trade check"]).startswith("Skip"):
        return pd.Series({"Qty": 0, "Invested": 0.0, "Max loss": 0.0, "Sizing note": "skipped: the chart's stop is too far away"})
    return pd.Series(entry_plan.position_size(row["Entry"], row["Stop"], capital, risk_pct / 100, max_pos_pct / 100))


if len(res):
    res = res.join(res.apply(_size, axis=1))
else:
    for _c in ("Qty", "Invested", "Max loss", "Sizing note"):
        res[_c] = pd.Series(dtype=float)

if "earn_dates" not in st.session_state:
    st.session_state.earn_dates = {}
shown_symbols = tuple(res["Stock"].head(60))
if len(res) and st.button(f"📅 Check results dates for the {len(shown_symbols)} stocks shown", help="Looks up each stock's next results date on Yahoo Finance (many small caps have none)."):
    st.session_state.earn_dates.update(_results_dates(shown_symbols))
res["Results note"] = res["Stock"].map(lambda s: earnings.describe(st.session_state.earn_dates[s]) if s in st.session_state.earn_dates else "")
res["Next results"] = res["Stock"].map(lambda s: (st.session_state.earn_dates[s].isoformat() if st.session_state.earn_dates.get(s) else "unknown") if s in st.session_state.earn_dates else "")

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
        cols = ["Stock", "Sector", "Status", "Trade check", "Verdict", "CMP", "Pivot", "Entry", "Buy up to", "Stop", "Risk %", "Qty", "Invested", "Max loss",
                "Next results", "Target 2R", "Target 3R", "Base weeks", "Base depth", "Contractions", "Shrinking", "Tightening", "Volume dried up",
                "Breakout quality", "Breakout note", "Volume to confirm", "Criteria met", "Missing", "RS Rating", "SMA 50", "SMA 150", "SMA 200", "200DMA up 4M",
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
                "Stop": st.column_config.NumberColumn(format="%.2f", help="Just under the low of the final pullback (not capped)"),
                "Risk %": st.column_config.NumberColumn(format="percent", help="Entry to the chart's stop. Over 10% = skip."),
                "Qty": st.column_config.NumberColumn(format="%.0f", help="Shares sized from your capital and loss limit in the sidebar"),
                "Invested": st.column_config.NumberColumn(format="₹%.0f"),
                "Max loss": st.column_config.NumberColumn(format="₹%.0f", help="About what you lose if stopped out"),
                "Next results": st.column_config.TextColumn(help="From Yahoo Finance after you press the check button; 'unknown' means Yahoo has no date"),
                "Base weeks": st.column_config.NumberColumn(format="%.0f", help="Weeks since the base's high"),
                "Base depth": st.column_config.NumberColumn(format="percent", help="From the base high to its lowest low. Over 35% is a deep, riskier base."),
                "Breakout quality": st.column_config.CheckboxColumn(help="Breakout day traded 1.4x+ average volume and closed in the top half of its range"),
                "Breakout note": st.column_config.TextColumn(width="large"),
                "Target 2R": st.column_config.NumberColumn(format="%.2f", help="Entry + 2x the risk"),
                "Target 3R": st.column_config.NumberColumn(format="%.2f", help="Entry + 3x the risk"),
                "Shrinking": st.column_config.CheckboxColumn(help="None of the last three pullbacks is more than 1.5 points deeper than the one before"),
                "Tightening": st.column_config.CheckboxColumn(help="The last pullback is under 8% and no more than 60% of the biggest one"),
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
        render_card(card, str(row["Sector"]).split(", ")[0], key_prefix="ttcard",
                    plan={**row[entry_plan.COLUMNS].to_dict(), **row[["Qty", "Invested", "Max loss", "Sizing note", "Results note"]].to_dict()})

with st.expander("How the trade plan is worked out"):
    st.markdown(
        f"""
- **Base:** the highest high of the last {entry_plan.LOOKBACK} sessions (before any breakout, ignoring the last {entry_plan.PROBE_SESSIONS} sessions' intraday probes) is the base's left peak; the base is everything since then.
  Under {entry_plan.MIN_BASE_SESSIONS} sessions (3 weeks) there is **no base**. Base depth is the peak to the lowest low since; over {entry_plan.DEEP_BASE:.0%} is a deep, riskier base.
- **Pivot (buy point):** that high. If the stock has already closed through the earlier highs in the last {entry_plan.BREAKOUT_LOOKBACK} sessions, the pivot is the high it broke through.
- **Entry:** just above the pivot (+0.1%); for a stock already through the pivot, the latest close while it is within {entry_plan.CHASE_LIMIT:.0%} above it. More than that is **extended**: wait for a new base.
- **Retesting:** it broke out in the last {entry_plan.BREAKOUT_LOOKBACK} sessions but has closed back under the pivot. Wait for a close back above it; if it closes under the stop the breakout failed.
- **Stop:** just under the low of the final pullback. It is **not capped**: up to {entry_plan.GOOD_STOP_RISK:.0%} is fine, up to {entry_plan.MAX_STOP_RISK:.0%} is wide, and beyond that the setup is a skip.
- **Shrinking / Tightening:** pullbacks are moves of {entry_plan.MIN_SWING:.0%}+ in closing price. Shrinking: of the last three, none is more than 1.5 points deeper than the one before.
  Tightening: the last is under 8% and no more than 60% of the biggest. The **textbook VCP filter** needs both plus Volume dried up (last 10 sessions' average volume under 80% of the 50-day average).
- **Breakout quality:** the breakout day should trade at least {entry_plan.BREAKOUT_VOL_X}x the 50-day average volume and close in the top half of its day's range.
- **Targets:** entry + 2 or 3 times the risk. Many winners are held much longer.
- **Position size:** shares = (your capital x your loss limit) / (entry - stop), never more than your largest-position share of capital. Set the three numbers in the sidebar.
- **Market check:** Nifty above its 50-day, 50-day above 200-day, and the 200-day rising = uptrend. Below the 200-day = downtrend. Minervini does not buy breakouts in a downtrend.
- **Results dates:** from Yahoo Finance when you press the button; many small caps have none, so check the company's announcement. A results gap can jump past any stop.
- **Not covered:** fundamentals (sales and earnings growth, margins), industry strength, and selling rules beyond the stop and targets. This is a mechanical reading of the chart, not a forecast or advice.
"""
    )
