"""Streamlit page: the Mean Reversion (RSI(2)) strategy - today's signals, paper trades with their exit status, and the rules with the backtest."""

import sys
from datetime import date
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.append(str(Path(__file__).resolve().parent.parent))
from strategies import market_trend, paper_trades, price_store  # noqa: E402
from strategies import mean_reversion as mr  # noqa: E402
from strategies.scan_all import scan_all_sectors  # noqa: E402
from strategies.scan_ui import paper_open_counts, screener_url, tradingview_url  # noqa: E402

st.set_page_config(page_title="Mean Reversion", page_icon="📉", layout="wide")

from strategies.auth_gate import require_password  # noqa: E402

require_password()
st.title("📉 Mean Reversion (RSI(2))")
st.caption(
    "Buy a stock in a long-term uptrend after it has fallen several sessions in a row, and sell as soon as it bounces back: usually 2 to 5 sessions. "
    "Practice it here with paper trades before using real money. Technicals only: the rule knows nothing about why a stock fell. Backtest results are past results, not a forecast. Not investment advice."
)

if st.session_state.get("mr_flash"):
    st.success(st.session_state.pop("mr_flash"))

full, _summaries = scan_all_sectors(0)
one = full.drop_duplicates("Stock").set_index("Stock")
sector_of, company_of = one["Sector"].to_dict(), one["Company"].to_dict()

st.sidebar.header("Mean Reversion")
slots = st.sidebar.number_input("Positions at a time", min_value=1, max_value=30, value=mr.SLOTS, step=1,
                                help="The backtest held up to 10 equal positions and took the lowest RSI(2) first when more stocks signalled than there were free slots.")
min_value = st.sidebar.slider("Minimum traded value (₹ cr/day)", 1.0, 50.0, mr.MIN_VALUE_CR, step=0.5,
                              help="The backtest used 5 crore a day. Thinner stocks cost more to trade and the results got worse.")
if st.sidebar.button("🔄 Recalculate", width="stretch"):
    st.cache_data.clear()
    st.rerun()

SYMBOLS = tuple(sorted(set(full["Stock"])))


@st.cache_data(ttl=600, show_spinner="Checking every stock's last few sessions...")
def _scan(rsi_max: float, min_drop: float, min_value_cr: float) -> tuple:
    hist = price_store.load_history(list(SYMBOLS))
    sig = mr.signals(hist, rsi_max=rsi_max, min_drop=min_drop, min_value_cr=min_value_cr)
    judged = sum(1 for s, h in hist.items() if not s.startswith("^") and mr.evaluate(h) is not None)
    latest = max((h.index[-1] for s, h in hist.items() if not s.startswith("^") and len(h)), default=None)
    return sig, judged, latest


@st.cache_data(ttl=300, show_spinner=False)
def _hist(symbols: tuple) -> dict:
    return price_store.load_history(list(symbols))


@st.cache_data(ttl=3600, show_spinner=False)
def _market() -> dict:
    return market_trend.nifty_trend()


def _money(v, signed=False):
    v = round(v)
    return f"{'+' if signed and v > 0 else ''}{'-' if v < 0 else ''}₹{abs(v):,.0f}"


def _pct(v):
    v = round(v, 2) + 0.0
    return f"{v:+.2f}%" if v else "0.00%"


def _colour(text, v):
    return f":green[{text}]" if v > 0 else (f":red[{text}]" if v < 0 else text)


def _tint(v):
    if pd.isna(v) or v == 0:
        return ""
    return "background-color: #c6efce; color: #006100" if v > 0 else "background-color: #ffc7ce; color: #9c0006"


try:
    trades = paper_trades.load()
except Exception as exc:
    st.error(f"Could not read the paper trades: {exc}")
    st.stop()
mine = trades[trades["strategy"].map(paper_trades.strategy_of) == paper_trades.MEANREV].copy()
mine["mr"] = [paper_trades.parse_signals(t).get("mr") or {} for t in mine["signals"]]
open_mine = mine[mine["sell_price"].isna()]
closed_mine = mine[mine["sell_price"].notna()]
pending = open_mine[[bool(m.get("pending")) for m in open_mine["mr"]]]
holding = open_mine.drop(pending.index)
held_symbols = set(open_mine["symbol"])
slots_used = len(open_mine)


# ---------------------------------------------------------------- actions
def _order(symbol: str, row: dict, rank: int) -> None:
    mkt = _market()
    snap = {"pending": True, "signal_session": str(row["Date"].date()), "signal_close": round(row["Close"], 2), "rsi2": round(row["RSI2"], 2),
            "drop3": round(row["Drop 3D"], 4), "vs200": round(row["vs 200DMA"], 4), "value_cr": round(row["Value Cr"], 2), "tier": row["Tier"],
            "est_cost_pct": round(row["Est. cost %"], 3), "rank": rank, "market": mkt.get("state")}
    try:
        qty = paper_trades.buy(symbol, sector_of.get(symbol, ""), row["Close"], str(row["Date"].date()), {"mr": snap}, strategy=paper_trades.MEANREV)
        counts = paper_open_counts()
        counts[symbol] = counts.get(symbol, 0) + 1
        st.session_state["mr_flash"] = (f"Order placed for {symbol}: about {qty} shares (₹{paper_trades.BUDGET:,} at the signal close of ₹{row['Close']:,.2f}). "
                                        "It fills at the next session's open: come back to 'My paper trades' after that session's prices are stored.")
    except Exception as exc:
        st.session_state["mr_flash"] = f"Could not place the order for {symbol}: {exc}"


def _fill(trade_id: str, symbol: str, snap: dict, hist: dict) -> str:
    fill = mr.entry_fill(hist.get(symbol), snap.get("signal_session"))
    if fill is None:
        return f"{symbol}: the next session is not stored yet."
    session, price = fill
    qty = paper_trades.quantity_for(price)
    if qty < 1:
        return f"{symbol}: opened at ₹{price:,.2f}, more than the ₹{paper_trades.BUDGET:,} limit for one share. Cancel the order."
    paper_trades.fill_entry(trade_id, price, session.isoformat(), qty,
                            {"mr": {**snap, "pending": False, "entry_session": session.isoformat(), "entry_open": round(price, 2),
                                    "gap_vs_signal_close": round(price / snap["signal_close"] - 1, 4)}})
    return f"{symbol}: bought {qty} at the open of {session}, ₹{price:,.2f} ({_pct((price / snap['signal_close'] - 1) * 100)} vs the signal close)."


def _fill_one(trade_id: str, symbol: str, snap: dict) -> None:
    try:
        st.session_state["mr_flash"] = _fill(trade_id, symbol, snap, _hist((symbol,)))
    except Exception as exc:
        st.session_state["mr_flash"] = f"Could not fill {symbol}: {exc}"


def _fill_all(items: list) -> None:
    msgs = []
    for trade_id, symbol, snap in items:
        try:
            msgs.append(_fill(trade_id, symbol, snap, _hist((symbol,))))
        except Exception as exc:
            msgs.append(f"{symbol}: {exc}")
    st.session_state["mr_flash"] = "  \n".join(msgs)


def _sell(trade_id: str, symbol: str, snap: dict, status: dict, buy_price: float, qty: int) -> None:
    price, session = status["sell_price"], status["sell_session"]
    try:
        paper_trades.sell(trade_id, price, session.isoformat(), on_date=session.isoformat(),
                          signals={"mr": {**snap, "exit_reason": status["state"], "exit_signal_session": status["signal_session"].isoformat(), "sell_open": round(price, 2),
                                          "sessions_held": status["day"]}})
        st.session_state["mr_flash"] = f"Sold {qty} x {symbol} at the open of {session}, ₹{price:,.2f}: {_pct((price / buy_price - 1) * 100)} before costs."
    except Exception as exc:
        st.session_state["mr_flash"] = f"Could not sell {symbol}: {exc}"


def _cancel(trade_id: str, symbol: str) -> None:
    try:
        paper_trades.delete(trade_id)
        st.session_state["mr_flash"] = f"Order for {symbol} cancelled."
        counts = paper_open_counts()
        counts[symbol] = max(0, counts.get(symbol, 1) - 1)
    except Exception as exc:
        st.session_state["mr_flash"] = f"Could not cancel: {exc}"


tab_sig, tab_trades, tab_rules = st.tabs(["📡 Today's signals", f"🧾 My paper trades ({len(open_mine)} open, {len(closed_mine)} closed)", "📖 Rules & backtest"])

# ================================================================ signals
with tab_sig:
    variant = st.radio(
        "Which signals", ["All RSI(2) below 5 (Strong ones first)", "Strong only: RSI(2) below 5 and a 3-day fall of 8% or more", "Wider: RSI(2) below 10 (weaker in the backtest)"],
        horizontal=True, help="Strong = the version with the best backtest result. The wider list is only for watching; it earned much less after costs.",
    )
    rsi_max, min_drop = (5.0, 0.0) if variant.startswith("All") else (5.0, mr.STRONG_DROP) if variant.startswith("Strong") else (10.0, 0.0)
    sig, judged, latest = _scan(rsi_max, min_drop, float(min_value))

    expected = price_store.expected_last_session()
    if latest is None:
        st.error("No prices are stored, so there is nothing to scan.")
        st.stop()
    behind = latest.date() < expected
    mkt = _market()
    free = max(slots - slots_used, 0)

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Signals today", len(sig), help="Stocks that meet the setup on the last stored session.")
    m2.metric("Strong", int((sig["Tier"] == "Strong").sum()) if len(sig) else 0)
    m3.metric("Free slots", f"{free} of {slots}", help="Open positions and waiting orders both use a slot.")
    m4.metric("Stocks judged", judged, help="Stocks with at least 205 sessions of stored prices and no suspicious price jump in the last week.")
    if behind:
        st.warning(f"Prices are stored up to **{latest.date()}**, but the last completed session was **{expected}**. The daily price job may not have run yet: signals may be a day old.")
    else:
        st.caption(f"Signals are as of the close on **{latest.date()}**. An order placed now fills at the open of the **next session**.")
    st.caption(f"Nifty today: {mkt['state']}. In the backtest the rule earned about the same in uptrends and sideways markets, so the market state is shown for information only.")

    if sig.empty:
        st.info("No stock meets the setup right now. That is normal: the strict version fires on a few stocks a week, and some weeks on none. Check again after the next close.")
    else:
        st.caption(f"{len(sig)} stock(s). The backtest filled free slots from the top: **lowest RSI(2) first**. Check the news for each before ordering: a stock down on a fraud report or a results shock looks the same to the rule as one down on market noise.")
        widths = [2.0, 0.8, 0.8, 0.9, 0.9, 0.9, 0.9, 0.8, 1.5, 1.4]
        head = st.columns(widths)
        for col, label in zip(head, ["Stock", "Tier", "RSI(2)", "3-day fall", "Close", "vs 200DMA", "₹ cr/day", "Est. cost", "Last 4 closes", ""]):
            col.markdown(f"**{label}**")
        for i, r in enumerate(sig.head(40).to_dict("records"), start=1):
            sym = r["Stock"]
            c = st.columns(widths, vertical_alignment="center")
            c[0].markdown(f"**{sym}**  \n<small>{company_of.get(sym, '')} · {sector_of.get(sym, '')}</small>  \n"
                          f"<small>[chart]({tradingview_url(sym)}) · [screener]({screener_url(sym)})</small>", unsafe_allow_html=True)
            c[1].write("🟢 Strong" if r["Tier"] == "Strong" else r["Tier"])
            c[2].write(f"{r['RSI2']:.1f}")
            c[3].write(f"{r['Drop 3D'] * 100:.1f}%")
            c[4].write(f"₹{r['Close']:,.2f}")
            c[5].write(f"{r['vs 200DMA'] * 100:+.1f}%")
            c[6].write(f"{r['Value Cr']:,.0f}")
            c[7].write(f"{r['Est. cost %']:.2f}%")
            c[8].write(r["Last closes"])
            holds = sym in held_symbols
            full_up = free <= 0
            label = "✅ Already in your trades" if holds else "No free slot" if full_up else "📝 Order for next open"
            c[9].button(label, key=f"mr_order_{sym}", on_click=_order, args=(sym, r, i), disabled=holds or full_up)
        if len(sig) > 40:
            st.caption(f"Showing the first 40 of {len(sig)}.")
        st.caption("Est. cost = statutory charges (about 0.25% round trip) plus a slippage allowance by liquidity (0.10% / 0.20% / 0.40%). Paper trades here do not deduct it; the review does.")

# ================================================================ paper trades
with tab_trades:
    ids = tuple(sorted(open_mine["symbol"].unique()))
    hist = _hist(ids) if ids else {}

    st.subheader(f"Orders waiting for the next open ({len(pending)})")
    if pending.empty:
        st.caption("None. An order placed on the Today's signals tab appears here until it is filled at the next session's open.")
    else:
        ready = []
        widths = [1.6, 1.2, 1.2, 2.6, 1.3, 0.9]
        head = st.columns(widths)
        for col, label in zip(head, ["Stock", "Signal session", "Signal close", "Fill", "", ""]):
            col.markdown(f"**{label}**")
        for r in pending.itertuples():
            snap = r.mr
            fill = mr.entry_fill(hist.get(r.symbol), snap.get("signal_session"))
            c = st.columns(widths, vertical_alignment="center")
            c[0].markdown(f"**{r.symbol}**  \n<small>{snap.get('tier', '')} · RSI(2) {snap.get('rsi2', '')}</small>", unsafe_allow_html=True)
            c[1].write(snap.get("signal_session", ""))
            c[2].write(f"₹{snap.get('signal_close', r.buy_price):,.2f}")
            if fill is None:
                c[3].caption("The next session is not stored yet. Come back after the next close.")
            else:
                gap = fill[1] / snap["signal_close"] - 1
                c[3].write(f"Opened ₹{fill[1]:,.2f} on {fill[0]} ({_pct(gap * 100)} vs signal close)")
                ready.append((r.id, r.symbol, snap))
            c[4].button("Fill at the open", key=f"mr_fill_{r.id}", on_click=_fill_one, args=(r.id, r.symbol, snap), disabled=fill is None)
            c[5].button("Cancel", key=f"mr_cancel_{r.id}", on_click=_cancel, args=(r.id, r.symbol))
        if len(ready) > 1:
            st.button(f"Fill all {len(ready)} ready orders", on_click=_fill_all, args=(ready,), type="primary")

    st.subheader(f"Open positions ({len(holding)})")
    if holding.empty:
        st.caption("No open positions.")
    else:
        widths = [1.6, 1.0, 1.0, 0.7, 1.1, 1.0, 0.9, 3.0, 1.5]
        head = st.columns(widths)
        for col, label in zip(head, ["Stock", "Bought on", "Buy price", "Qty", "Invested", "Last close", "Gain/loss", "Exit status", ""]):
            col.markdown(f"**{label}**")
        for r in holding.sort_values("buy_date", ascending=False).itertuples():
            snap = r.mr
            h = hist.get(r.symbol)
            st_ = mr.exit_status(h, r.buy_session or r.buy_date) if h is not None else {"day": 0, "state": "hold", "close": float("nan")}
            now = st_["close"] if st_["day"] else (float(h["Close"].dropna().iloc[-1]) if h is not None and len(h) else float("nan"))
            pct = (now / r.buy_price - 1) * 100 if not pd.isna(now) else float("nan")
            c = st.columns(widths, vertical_alignment="center")
            c[0].markdown(f"**{r.symbol}**  \n<small>{snap.get('tier', '')}</small>", unsafe_allow_html=True)
            c[1].write(r.buy_date)
            c[2].write(f"₹{r.buy_price:,.2f}")
            c[3].write(f"{r.qty}")
            c[4].write(_money(r.buy_price * r.qty))
            c[5].write("n/a" if pd.isna(now) else f"₹{now:,.2f}")
            c[6].markdown("n/a" if pd.isna(pct) else _colour(f"**{_pct(pct)}**", round(pct, 2)))
            can_sell = False
            if st_["state"] == "hold":
                if st_["day"]:
                    c[7].write(f"Session {st_['day']} of {mr.MAX_HOLD}. Sell once a session closes above its 5-day average (now ₹{st_['sma5']:,.2f}, last close ₹{st_['close']:,.2f}), or after session {mr.MAX_HOLD}.")
                else:
                    c[7].caption("Entry session not found in the stored prices.")
            else:
                why = "was above its 5-day average" if st_["state"] == "bounce" else f"ended session {mr.MAX_HOLD}, the time limit"
                if st_["sell_price"] is not None:
                    c[7].write(f"**Exit:** the {st_['signal_session']} close {why}. Sell at the {st_['sell_session']} open: ₹{st_['sell_price']:,.2f}.")
                    can_sell = True
                else:
                    c[7].write(f"**Exit signalled:** the {st_['signal_session']} close {why}. Sell at the next session's open (available after that session closes).")
            if can_sell:
                c[8].button("Sell at the open", key=f"mr_sell_{r.id}", on_click=_sell, args=(r.id, r.symbol, snap, st_, r.buy_price, r.qty), type="primary")
        st.caption("Sells are only recorded when you press the button; nothing is sold automatically. Sessions are counted with the entry session as session 1.")

    st.subheader("Closed trades")
    if closed_mine.empty:
        st.caption("Nothing sold yet.")
    else:
        rows = []
        for r in closed_mine.itertuples():
            snap = r.mr
            cost = (snap.get("est_cost_pct") or 0.4) / 100
            gross = r.sell_price / r.buy_price - 1
            rows.append({"Stock": r.symbol, "Tier": snap.get("tier", ""), "Bought": r.buy_date, "Sold": r.sell_date, "Sessions": snap.get("sessions_held"),
                         "Exit": {"bounce": "Bounce", "time": f"{mr.MAX_HOLD}-session limit"}.get(snap.get("exit_reason"), ""), "Buy": r.buy_price, "Sell": r.sell_price, "Qty": r.qty,
                         "Gross %": gross * 100, "Est. costs %": cost * 100, "Net %": (gross - cost) * 100,
                         "Net ₹": (r.sell_price - r.buy_price) * r.qty - cost * r.buy_price * r.qty, "Gap at entry %": (snap.get("gap_vs_signal_close") or 0) * 100})
        ct = pd.DataFrame(rows).sort_values("Sold", ascending=False).reset_index(drop=True)
        net = ct["Net %"]
        a, b, c_, d, e = st.columns(5)
        a.metric("Closed trades", len(ct))
        b.metric("Winners (after est. costs)", f"{int((net > 0).sum())} ({(net > 0).mean():.0%})")
        c_.metric("Average per trade (net)", _pct(net.mean()))
        d.metric("Average gross", _pct(ct["Gross %"].mean()))
        e.metric("Net P/L", _money(ct["Net ₹"].sum(), signed=True))
        if len(ct) < 30:
            st.caption("⚠️ Fewer than about 30 trades: a good or bad run this short says very little. The backtest had thousands of trades and still lost money in some whole years.")
        st.dataframe(ct.style.map(_tint, subset=["Gross %", "Net %", "Net ₹"]), hide_index=True, width="stretch",
                     column_config={"Buy": st.column_config.NumberColumn(format="₹%.2f"), "Sell": st.column_config.NumberColumn(format="₹%.2f"),
                                    "Gross %": st.column_config.NumberColumn(format="%.2f%%"), "Est. costs %": st.column_config.NumberColumn(format="%.2f%%"),
                                    "Net %": st.column_config.NumberColumn(format="%.2f%%"), "Net ₹": st.column_config.NumberColumn(format="₹%.0f"),
                                    "Gap at entry %": st.column_config.NumberColumn(format="%.2f%%", help="Open of the entry session vs the signal day's close")})
        by = ct.groupby("Tier").agg(Trades=("Stock", "count"), **{"Win rate %": ("Net %", lambda v: (v > 0).mean() * 100), "Avg net %": ("Net %", "mean")}).reset_index()
        by["Backtest win rate %"] = by["Tier"].map({"Strong": 69.0, "Basic": 62.5})
        by["Backtest avg net %"] = by["Tier"].map({"Strong": 1.93, "Basic": 0.29})
        st.markdown("**By tier, next to what the backtest averaged**")
        st.dataframe(by, hide_index=True, width="stretch", column_config={c: st.column_config.NumberColumn(format="%.2f") for c in ["Win rate %", "Avg net %", "Backtest win rate %", "Backtest avg net %"]})
        st.caption("Backtest figures are per trade after estimated costs, before tax: Strong = 6,063 trades, Basic (RSI(2) below 5 but a 3-day fall under 8%) = 30,780 trades.")

    with st.expander("Made a mistake? Remove a mean-reversion trade"):
        labels = {r.id: f"{r.symbol} - {r.buy_date} at ₹{r.buy_price:,.2f}{' (waiting for fill)' if r.id in set(pending['id']) else '' if pd.isna(r.sell_price) else ' (sold)'}" for r in mine.itertuples()}
        chosen = st.selectbox("Trade", list(labels), index=None, format_func=labels.get, placeholder="Choose a trade...")
        if st.button("🗑️ Delete this trade", disabled=chosen is None):
            paper_trades.delete(chosen)
            paper_open_counts(refresh=True)
            st.session_state["mr_flash"] = "Trade removed."
            st.rerun()

# ================================================================ rules
with tab_rules:
    st.subheader("The rule")
    st.markdown(
        f"""
**Setup** (judged on a day's close):
1. The stock closes **above its 200-day average**: it is in a long-term uptrend, so the fall is more likely a dip than a collapse.
2. **RSI(2) is below {mr.RSI_MAX:g}.** RSI(2) is a 0-100 score of the last two or three sessions: low means the stock has fallen almost every session recently. It measures
   *how consecutive* the falls were, not how big (a stock down 1.4% over three sessions can score 2.7).
3. Trades at least **₹{mr.MIN_VALUE_CR:g} crore a day** (so you can get in and out).
4. **Strong** setups also fell **{mr.STRONG_DROP:.0%} or more over the last 3 sessions**.

**Entry:** the **next session's open**. The signal is only known after the close, so you cannot buy at that close. (Here: place the order, then fill it once the next session's prices are stored.)

**Exit**, whichever comes first (the entry session counts as session 1):
- a session closes **above its 5-day average**: the bounce has happened; or
- session **{mr.MAX_HOLD}** ends: the time limit.
Sell at the **open of the session after** the exit signal. There is **no price stop**: in the backtest, stops made results worse at every level tested (a tight stop sold the dips that usually recover).

**Sizing:** up to {mr.SLOTS} equal positions, one per stock, taking the lowest RSI(2) first. Paper trades here use the app's usual ₹{paper_trades.BUDGET:,} per buy.

**Worked example** (SCI, March 2026): the closes ran 256.1, 246.2, 242.0, 241.4 (RSI(2) 3.0, above its 200-day average of 226.8). Bought at the next open, 9 Mar, ₹234.00.
That day it closed ₹232.65, under its 5-day average (₹243.68), so it was held. On 10 Mar it closed ₹245.25, above the 5-day average (₹241.50): sold at the 11 Mar open, ₹246.40, **+5.3%** before costs in 2 sessions.
"""
    )
    st.subheader("What the backtest found")
    st.caption("Stored list of 778 NSE stocks with daily prices, Oct 2015 to Sep 2026, 10 positions, idle cash earning 6.5%, estimated costs per the tiers above and 20.8% tax on each year's net profit. Nifty buy-and-hold over the same period: about 9.8% a year.")
    st.dataframe(pd.DataFrame(mr.BACKTEST_ROWS), hide_index=True, width="stretch",
                 column_config={"Win rate": st.column_config.NumberColumn(format="%.1f%%"), "Avg per trade, net of costs": st.column_config.NumberColumn(format="%.2f%%"),
                                "CAGR before tax": st.column_config.NumberColumn(format="%.1f%%"), "CAGR after tax": st.column_config.NumberColumn(format="%.1f%%"),
                                "Max drawdown": st.column_config.NumberColumn(format="%.1f%%")})
    st.markdown(
        """
- **Costs are the whole game.** The edge is about half a percent per trade for the basic version. With costs 1.5 times higher the basic version fell from 13.5% to 4.3% a year after tax; the Strong version held up better (17.8% to 14.3%).
- **Stops hurt.** Every price stop tested lowered the result and none reduced the portfolio drawdown.
- **It is not a sideways-market strategy.** It earned about the same per trade in uptrends and sideways markets, and the sideways-only version made less because it was rarely invested.
- **Uneven years:** large gains in 2021 and 2023, losses in 2018 and 2022 for the basic version. The second half of the period (2021 on) earned much more than the first.
- **20-30% a year was not supported** once realistic costs and tax were included. 13-18% a year after tax, with drawdowns of 30-45%, is what the test supports.
"""
    )
    with st.expander("Weaknesses to keep in mind"):
        st.markdown(
            """
- The stock list is today's list: stocks that crashed and were delisted are missing, which flatters a rule that buys crashes.
- I looked at several variants (thresholds, stops, slot counts, liquidity) before settling on these; the best-looking one is partly luck. Paper trading is the out-of-sample test.
- Slippage and gaps are estimated, not measured. Real fills in thin stocks can be worse.
- Tax: 20% short-term capital gains was assumed. If frequent trading is treated as business income, tax would be higher.
- It needs a daily routine: check after the close, place orders, fill and sell on the following sessions.
- The rule has no view on news, results dates or fundamentals.
"""
        )
    st.subheader("How to use it day to day")
    st.markdown(
        """
1. **After the close** (once the daily price job has run, usually by early evening), open **Today's signals**. Check the news for the top names, then press **Order for next open** for as many as you have free slots.
2. **After the next session closes**, open **My paper trades**: orders show the price the stock really opened at. Press **Fill at the open**.
3. **Each evening**, look at **Open positions**. When a position shows an exit, press **Sell at the open** (the sell price is the open of the session after the exit signal).
4. Judge it only after **30 or more closed trades**, and compare the Strong tier with the backtest's averages.
"""
    )
