"""Streamlit page: how the Trend Template page's own buy signals would have fared over the past years (stored backtest results)."""

import sys
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.append(str(Path(__file__).resolve().parent.parent))
from strategies import backtest_store, market_trend, minervini_backtest  # noqa: E402
from strategies.scan_ui import screener_url, tradingview_url  # noqa: E402

st.set_page_config(page_title="Minervini Backtest", page_icon="🧪", layout="wide")

from strategies.auth_gate import require_password  # noqa: E402

require_password()
st.title("🧪 Minervini backtest")
st.caption(
    "What would have happened if every Trend Template breakout signal had been bought and sold automatically at 2R (a gain of twice the risk), with the chart's stop as the "
    "loss exit. The trades come from the backtest script and are kept in the database; this page only reads and slices them. Past results are not a forecast. Not investment advice."
)


@st.cache_data(ttl=600, show_spinner=False)
def _runs() -> pd.DataFrame:
    return backtest_store.list_runs()


@st.cache_data(ttl=600, show_spinner="Loading the backtest trades...")
def _trades(run_id: str) -> pd.DataFrame:
    return backtest_store.load_trades(run_id)


runs = _runs()
if runs.empty:
    st.info("No backtest has been run yet. Run `python scripts/run_minervini_backtest.py` on your computer; the results appear here after that.")
    st.stop()

run = runs.iloc[0]
if len(runs) > 1:
    pick = st.sidebar.selectbox("Backtest run", runs["run_id"], format_func=lambda r: f"{runs.set_index('run_id').loc[r, 'created_at'][:16].replace('T', ' ')} UTC")
    run = runs[runs["run_id"] == pick].iloc[0]
all_trades = _trades(run["run_id"])
st.caption(f"Run of {run['created_at'][:16].replace('T', ' ')} UTC: {run['stocks']} stocks, prices from {run['first_date']} to {run['last_date']}, {run['signals']:,} signals found in all.")

st.sidebar.header("Which trades to count")
states = st.sidebar.multiselect("Nifty trend on the buy day", ["Uptrend", "Mixed", "Downtrend"], default=["Uptrend"],
                                help="Minervini only buys breakouts when the market is in an uptrend. Pick the other states to see what happens without that rule.")
has_breadth = all_trades["breadth50"].notna().any()
if has_breadth:
    min_breadth = st.sidebar.slider(
        "Stocks above their 50-day average on the buy day (at least %)", 0, 90, int(market_trend.BREADTH_MIN * 100), step=5,
        help="Market breadth. In this backtest the results were much better when most stocks were in an uptrend themselves, not just the Nifty. 0 switches the rule off. "
             "The Trend Template page shows today's reading.")
else:
    min_breadth = 0
    st.sidebar.caption("This older run has no breadth figures. Run the backtest script again to get them.")
stop_ok = st.sidebar.checkbox("Chart stop within 10% of the entry", value=True,
                              help="The Trend Template page marks a setup as a skip when the stop is further than 10% away. Untick to include those.")
good_break = st.sidebar.checkbox("Strong breakout only (volume 1.4x+, close in top half)", value=False)
vcp_only = st.sidebar.checkbox("Textbook VCP only (shrinking, tight, volume dried up)", value=False)
one_at_a_time = st.sidebar.checkbox("One trade per stock at a time", value=True,
                                    help="A stock that signals again while its earlier trade is still open is not counted twice.")

t = all_trades
if stop_ok:
    t = t[~t["trade_check"].astype(str).str.startswith("Skip")]
if good_break:
    t = t[t["breakout_quality"] == 1]
if vcp_only:
    t = t[(t["shrinking"] == 1) & (t["tightening"] == 1) & (t["volume_dried_up"] == 1)]
before_market = t  # kept for the "does the market filter matter" comparisons
t = t[t["breadth50"] >= min_breadth / 100] if min_breadth else t
other_states = t  # for the Nifty comparison: breadth rule applied, Nifty state free
t = t[t["nifty_state"].isin(states)] if states else t.iloc[0:0]
if one_at_a_time and len(t):
    t = minervini_backtest.non_overlapping(t)


def summarise(g: pd.DataFrame) -> pd.Series:
    wins, losses = int((g["outcome"] == "Win").sum()), int((g["outcome"] == "Loss").sum())
    closed = g[g["outcome"] != "Open"]
    return pd.Series({
        "Trades": len(g), "Wins": wins, "Losses": losses, "Open": int((g["outcome"] == "Open").sum()),
        "Win rate": wins / (wins + losses) if wins + losses else float("nan"),
        "Avg R": closed["r_multiple"].mean() if len(closed) else float("nan"),
        "Total R": closed["r_multiple"].sum() if len(closed) else 0.0,
        "Median days": g["days_held"].median() if len(g) else float("nan"),
    })


if t.empty:
    st.warning("No trades match these settings.")
    st.stop()

s = summarise(t)
m1, m2, m3, m4, m5, m6 = st.columns(6)
m1.metric("Trades", f"{int(s['Trades']):,}")
m2.metric("✅ Reached 2R", f"{int(s['Wins']):,}")
m3.metric("❌ Hit the stop", f"{int(s['Losses']):,}", help="Includes gap-downs, which lose more than 1R.")
m4.metric("⏳ Still open", f"{int(s['Open']):,}", help="Neither exit reached by the last date in the data. They are left out of the win rate and the R averages.")
m5.metric("Win rate", f"{s['Win rate']:.0%}", help="Wins out of wins plus losses. With a 2R target and a 1R stop a trade only needs to win 1 time in 3 (33%) to break even, before costs.")
m6.metric("Average R", f"{s['Avg R']:+.2f}", help="Average result per closed trade in multiples of the risk taken (1R = loss at the stop, +2R = target). Above 0 means the signals paid off.")
st.caption(
    f"Breakeven win rate for a 2R target is 33%. Total result of these trades: {s['Total R']:+.1f}R across the closed ones, median holding time {s['Median days']:.0f} sessions. "
    "The stop sells at the stop price, or at the open if the stock gaps below it, so some losses are bigger than 1R."
)

tab_stock, tab_when, tab_trades, tab_how = st.tabs(["📊 By stock", "🗓️ By year & market", "📋 All trades", "ℹ️ How it works"])

with tab_stock:
    per = t.groupby("symbol").apply(summarise)
    sector_of = t.groupby("symbol")["sector"].first()
    last_signal = t.groupby("symbol")["signal_date"].max()
    per = per.join(sector_of.rename("Sector")).join(last_signal.rename("Last signal")).reset_index().rename(columns={"symbol": "Stock"})

    def flag(r):
        closed = r["Wins"] + r["Losses"]
        if closed < 3:
            return "Too few trades" if closed else ("Open trade" if r["Open"] else "-")
        if r["Total R"] > 0 and r["Win rate"] >= 0.5:
            return "✅ Worked"
        if r["Total R"] > 0:
            return "🟡 Paid, but mostly stopped out"
        return "❌ Did not work"

    per["Flag"] = per.apply(flag, axis=1)
    c1, c2, c3 = st.columns([1.2, 1.2, 2])
    min_closed = c1.number_input("At least this many closed trades", min_value=1, max_value=20, value=1, step=1)
    flags = c2.multiselect("Flag", ["✅ Worked", "🟡 Paid, but mostly stopped out", "❌ Did not work", "Too few trades", "Open trade"], placeholder="Any flag")
    sector_pick = c3.multiselect("Sectors", sorted({x for v in per["Sector"] for x in str(v).split(", ") if x}), placeholder="All sectors")
    view = per[(per["Wins"] + per["Losses"]) >= min_closed]
    if flags:
        view = view[view["Flag"].isin(flags)]
    if sector_pick:
        view = view[view["Sector"].map(lambda v: any(x in sector_pick for x in str(v).split(", ")))]
    view = view.sort_values(["Total R", "Trades"], ascending=False)
    view = view[["Stock", "Sector", "Trades", "Wins", "Losses", "Open", "Win rate", "Avg R", "Total R", "Median days", "Last signal", "Flag"]]
    view.insert(1, "TradingView", view["Stock"].map(tradingview_url))
    st.caption(f"{len(view)} stocks. Flag: ✅ needs 3+ closed trades, a positive total and half or more reaching 2R; 🟡 positive total but under half reached 2R; ❌ a zero or negative total. "
               "Fewer than 3 closed trades is too few to say anything. Click a header to sort or use the table's search icon.")
    st.dataframe(
        view,
        column_config={
            "TradingView": st.column_config.LinkColumn("TradingView", display_text="📈 Chart"),
            "Win rate": st.column_config.NumberColumn(format="percent"),
            "Avg R": st.column_config.NumberColumn(format="%+.2f", help="Average result per closed trade, in multiples of the risk"),
            "Total R": st.column_config.NumberColumn(format="%+.1f"),
            "Median days": st.column_config.NumberColumn(format="%.0f", help="Sessions from the buy to the exit"),
        },
        hide_index=True, width="stretch", height=min(35 * (len(view) + 1) + 3, 650),
    )

with tab_when:
    st.subheader("Does the breadth rule matter?")
    st.caption("Same signals and the same other settings (Nifty trend, stop, quality, VCP, one trade at a time), split by the share of stocks above their 50-day average on the buy day.")
    if has_breadth:
        base = before_market[before_market["nifty_state"].isin(states)] if states else before_market.iloc[0:0]
        bparts = []
        for label, lo, hi in [("Under 50%", 0.0, 0.5), ("50% to 70%", 0.5, 0.7), ("70% or more", 0.7, 1.01)]:
            g = base[(base["breadth50"] >= lo) & (base["breadth50"] < hi)]
            if one_at_a_time and len(g):
                g = minervini_backtest.non_overlapping(g)
            if len(g):
                bparts.append(summarise(g).rename(label))
        if bparts:
            st.dataframe(pd.DataFrame(bparts).rename_axis("Stocks above 50-day average").reset_index(),
                         column_config={"Win rate": st.column_config.NumberColumn(format="percent"), "Avg R": st.column_config.NumberColumn(format="%+.2f"),
                                        "Total R": st.column_config.NumberColumn(format="%+.1f"), "Median days": st.column_config.NumberColumn(format="%.0f")},
                         hide_index=True, width="stretch")
        st.caption("This is the pattern that led to the breadth rule, so it is not an independent test: I looked at about 15 market filters and kept the one that helped most. "
                   "The improvement did show up in both 2016-2020 and 2021-2026, but treat it as a promising lead, not a proven edge.")
    st.subheader("Does the Nifty rule matter?")
    st.caption("Same signals and the same other settings (breadth, stop, quality, VCP, one trade at a time), split by the Nifty trend on the buy day.")
    parts = []
    for state in ["Uptrend", "Mixed", "Downtrend"]:
        g = other_states[other_states["nifty_state"] == state]
        if one_at_a_time and len(g):
            g = minervini_backtest.non_overlapping(g)
        if len(g):
            parts.append(summarise(g).rename(state))
    if parts:
        st.dataframe(pd.DataFrame(parts).rename_axis("Nifty on the buy day").reset_index(),
                     column_config={"Win rate": st.column_config.NumberColumn(format="percent"), "Avg R": st.column_config.NumberColumn(format="%+.2f"),
                                    "Total R": st.column_config.NumberColumn(format="%+.1f"), "Median days": st.column_config.NumberColumn(format="%.0f")},
                     hide_index=True, width="stretch")
    st.subheader("Year by year (the trades you selected)")
    yearly = t.assign(Year=t["signal_date"].str[:4]).groupby("Year").apply(summarise).reset_index()
    st.dataframe(yearly, column_config={"Win rate": st.column_config.NumberColumn(format="percent"), "Avg R": st.column_config.NumberColumn(format="%+.2f"),
                                        "Total R": st.column_config.NumberColumn(format="%+.1f"), "Median days": st.column_config.NumberColumn(format="%.0f")},
                 hide_index=True, width="stretch")
    st.caption("Results swing a lot from year to year: breakout systems pay in strong markets and bleed small losses in choppy ones. A few dozen trades a year is not much to judge by.")
    st.subheader("What the winners and losers looked like")
    closed = t[t["outcome"] != "Open"]
    rows = []
    for label, mask in [("Strong breakout (volume + close)", closed["breakout_quality"] == 1), ("Weak breakout", closed["breakout_quality"] == 0),
                        ("Textbook VCP", (closed["shrinking"] == 1) & (closed["tightening"] == 1) & (closed["volume_dried_up"] == 1)),
                        ("Not textbook VCP", ~((closed["shrinking"] == 1) & (closed["tightening"] == 1) & (closed["volume_dried_up"] == 1))),
                        ("Stop within 8%", closed["risk_pct"] <= 0.08), ("Stop 8-10%", (closed["risk_pct"] > 0.08) & (closed["risk_pct"] <= 0.10)),
                        ("Stop beyond 10%", closed["risk_pct"] > 0.10), ("RS rating 90+", closed["rs_rating"] >= 90), ("RS rating 70-89", closed["rs_rating"] < 90)]:
        g = closed[mask]
        if len(g):
            rows.append(summarise(g).rename(label))
    st.dataframe(pd.DataFrame(rows).drop(columns=["Open"]).rename_axis("Setup").reset_index(),
                 column_config={"Win rate": st.column_config.NumberColumn(format="percent"), "Avg R": st.column_config.NumberColumn(format="%+.2f"),
                                "Total R": st.column_config.NumberColumn(format="%+.1f"), "Median days": st.column_config.NumberColumn(format="%.0f")},
                 hide_index=True, width="stretch")

with tab_trades:
    show = t.copy()
    show["Result"] = show["outcome"].map({"Win": "✅ 2R reached", "Loss": "❌ Stopped out", "Open": "⏳ Open"})
    show = show.rename(columns={"symbol": "Stock", "sector": "Sector", "signal_date": "Bought", "nifty_state": "Nifty", "entry": "Entry", "stop": "Stop", "target": "Target 2R",
                                "risk_pct": "Risk %", "exit_date": "Exit date", "exit_price": "Exit price", "days_held": "Days", "r_multiple": "R", "pct_return": "Return",
                                "max_gain": "Best gain", "max_drop": "Worst drop", "rs_rating": "RS", "base_weeks": "Base weeks", "base_depth": "Base depth",
                                "contractions": "Pullbacks", "breakout_vol_x": "Breakout vol x"})
    show["Gap exit"] = show["gap_exit"] == 1
    show["Breakout OK"] = show["breakout_quality"] == 1
    show["VCP"] = (show["shrinking"] == 1) & (show["tightening"] == 1) & (show["volume_dried_up"] == 1)
    cols = ["Stock", "Sector", "Bought", "Nifty", "Result", "Entry", "Stop", "Target 2R", "Risk %", "Exit date", "Exit price", "Days", "R", "Return", "Best gain", "Worst drop",
            "Gap exit", "RS", "Base weeks", "Base depth", "Pullbacks", "Breakout OK", "Breakout vol x", "VCP"]
    show = show[cols].sort_values("Bought", ascending=False)
    show.insert(1, "TradingView", show["Stock"].map(tradingview_url))
    show.insert(2, "Screener", show["Stock"].map(screener_url))
    st.caption("Newest first. Best gain / Worst drop are the highest and lowest prices reached while the trade was open, relative to the entry.")
    st.dataframe(
        show,
        column_config={
            "TradingView": st.column_config.LinkColumn("TradingView", display_text="📈 Chart"),
            "Screener": st.column_config.LinkColumn("Screener", display_text="📊 Screener"),
            "Entry": st.column_config.NumberColumn(format="%.2f"), "Stop": st.column_config.NumberColumn(format="%.2f"),
            "Target 2R": st.column_config.NumberColumn(format="%.2f"), "Exit price": st.column_config.NumberColumn(format="%.2f"),
            "Risk %": st.column_config.NumberColumn(format="percent"), "Return": st.column_config.NumberColumn(format="percent"),
            "Best gain": st.column_config.NumberColumn(format="percent"), "Worst drop": st.column_config.NumberColumn(format="percent"),
            "R": st.column_config.NumberColumn(format="%+.2f"), "RS": st.column_config.NumberColumn(format="%.0f"),
            "Base weeks": st.column_config.NumberColumn(format="%.0f"), "Base depth": st.column_config.NumberColumn(format="percent"),
            "Breakout vol x": st.column_config.NumberColumn(format="%.1fx"),
        },
        hide_index=True, width="stretch", height=min(35 * (len(show) + 1) + 3, 700),
    )
    st.download_button("⬇️ Download these trades as CSV", data=show.drop(columns=["TradingView", "Screener"]).to_csv(index=False).encode("utf-8"),
                       file_name="minervini_backtest_trades.csv", mime="text/csv")

with tab_how:
    st.markdown(
        f"""
**A signal** is a day on which a stock, judged only on prices up to that day's close:
- passes all eight Trend Template conditions (the RS rating is ranked against the stocks that had enough history on that day),
- trades at least ₹{minervini_backtest.MIN_LIQUIDITY_CR} crore a day and had no one-day fall of 35% or more in the last year (the Trend Template page hides those too),
- and the page's entry plan reads it as **"Broke out" on the first day of the breakout**: a close through the highest high of the base.

**The trade:** bought at the plan's entry (that day's close), the stop is the plan's chart stop (just under the final pullback's low, not capped), and the target is entry + 2 x the risk. Each day after the buy:
- the day opens at or below the stop: sold at the open (a gap loss, worse than 1R); opens at or above the target: sold at the open;
- otherwise the day's low touching the stop sells at the stop, and the day's high touching the target sells at the target;
- a day that touches both counts as a loss, because the order within the day is unknown;
- a trade that has done neither by the last date in the data is **Open** and is left out of the win rate and the R averages.

**Market rule:** the Nifty state on the buy day uses the same rule as the Trend Template page's banner: uptrend = Nifty above its 50-day average, 50-day above 200-day, 200-day rising over the last month. The default is to count only uptrend buys. After a buy the trade is held to its exit whatever the market does.

**Breadth rule:** on each buy day the share of stocks (in the sector lists) that closed above their own 50-day average is stored with the trade. The sidebar slider keeps only buys made when at least that share was above it; 70% is the default
because it gave the best results in this data. Today's reading is on the Trend Template page. The share is measured on today's stock list, which makes past breadth look somewhat stronger than it really was.

**R** is the result in multiples of the risk taken: -1R is a loss at the stop, +2R a win at the target.

**Things that flatter or distort the results:**
- The stock list is today's sector lists. Companies that were delisted or fell out of the lists are missing, which makes results look better than they were.
- No brokerage, taxes or slippage; buys are assumed to fill at the breakout day's close, which in real trading you only know after the day ends.
- Prices are Yahoo's, adjusted for splits and bonuses; a few bad prints remain.
- There are no fundamentals, no results-date check and no selling rules beyond the stop and the 2R target, so it is Minervini's chart rules in a simplified form, not his whole method.
- Several signals can fall on the same days, so the total R is not the profit of a real account with a limited amount of money.
"""
    )
