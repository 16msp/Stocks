"""Streamlit page: paper-trading record in two sections: trades from the scanner / screener cards, and trades from the Minervini Trend Template cards."""

import sys
from datetime import date
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.append(str(Path(__file__).resolve().parent.parent))
from strategies import paper_trades, price_store  # noqa: E402
from strategies.scan_all import scan_all_sectors  # noqa: E402
from strategies.scan_ui import latest_session  # noqa: E402

st.set_page_config(page_title="Paper Trades", page_icon="🛒", layout="wide")

from strategies.auth_gate import require_password  # noqa: E402

require_password()
st.title("🛒 Paper Trades")
st.caption(
    f"Practice trading with no real money. Each buy spends at most **₹{paper_trades.BUDGET:,}** (shares are worked out for you), "
    "and you can hold up to **3 positions per stock**, and each sell closes one whole position. Trades happen at the **last completed daily close**, with no brokerage, taxes or "
    "slippage, so real results will be a little worse. Buy with the 🛒 on any card in the Sector Scanner, Favourites or Screener (first section), "
    "or on a Trend Template card (the **Minervini section**, which is reviewed against its own plan: pivot, stop, targets and market trend)."
)

full, _summaries = scan_all_sectors(0)
price_of = dict(zip(full["Stock"], full["CMP"]))
sector_of = full.drop_duplicates("Stock").set_index("Stock")["Sector"].to_dict()
company_of = full.drop_duplicates("Stock").set_index("Stock")["Company"].to_dict()
row_of = full.drop_duplicates("Stock").set_index("Stock", drop=False).to_dict("index")  # a stock's scan row = its technicals today

try:
    trades = paper_trades.load()
except Exception as exc:
    st.error(f"Could not read the paper trades: {exc}")
    st.stop()
held = trades.loc[trades["sell_price"].isna(), "symbol"].value_counts().to_dict()  # {symbol: open positions}
st.session_state["paper_open"] = dict(held)  # keep the 🛒 / ✅ state on the cards in sync without another query

if st.session_state.get("paper_flash"):
    st.success(st.session_state.pop("paper_flash"))

st.sidebar.header("Paper Trades")
if st.sidebar.button("🔄 Reload prices", width="stretch"):
    scan_all_sectors.clear()
    st.rerun()


def _money(v, signed=False):
    v = round(v)  # avoids "-₹0" from float noise
    return f"{'+' if signed and v > 0 else ''}{'-' if v < 0 else ''}₹{abs(v):,.0f}"


def _pct(v):
    v = round(v, 2) + 0.0
    return f"{v:+.2f}%" if v else "0.00%"


def _colour(text, v):
    return f":green[{text}]" if v > 0 else (f":red[{text}]" if v < 0 else text)


def _buy(symbol: str) -> None:
    price = price_of.get(symbol)
    try:
        qty = paper_trades.buy(symbol, sector_of.get(symbol, ""), price, latest_session(symbol), paper_trades.snapshot(row_of[symbol]))
        st.session_state["paper_flash"] = f"Bought {qty} x {symbol} at ₹{price:,.2f} = ₹{qty * price:,.0f}."
    except Exception as exc:
        st.session_state["paper_flash"] = f"Could not buy {symbol}: {exc}"


def _sell(trade_id: str, symbol: str, buy_price: float, qty: int) -> None:
    price = price_of.get(symbol)
    if price is None or pd.isna(price):
        st.session_state["paper_flash"] = f"No current price for {symbol}, so it cannot be sold right now."
        return
    try:
        paper_trades.sell(trade_id, price, latest_session(symbol))
        st.session_state["paper_flash"] = (
            f"Sold {qty} x {symbol} at ₹{price:,.2f}: {_pct((price / buy_price - 1) * 100)} ({_money((price - buy_price) * qty, signed=True)})."
        )
    except Exception as exc:
        st.session_state["paper_flash"] = f"Could not sell {symbol}: {exc}"



trades["section"] = trades["strategy"].map(paper_trades.strategy_of)
SIGNAL_NUM = [c for c in paper_trades.SIGNAL_FIELDS if c not in paper_trades.TEXT_SIGNALS and c != "Buy Score"]
PCT_SIGNALS = {"52W High Drop", "Off 52W Low", "RS 1M", "RS 3M", "vs 200DMA", "Overhead Supply", "Supply to +25%", "vs POC", "Worst 1D Move"}


@st.cache_data(ttl=900, show_spinner=False)
def _history(symbols: tuple) -> dict:
    return price_store.load_history(list(symbols))


def _tint(v):
    if pd.isna(v) or v == 0:
        return ""
    return "background-color: #c6efce; color: #006100" if v > 0 else "background-color: #ffc7ce; color: #9c0006"


def _now_price(symbol):
    now = price_of.get(symbol)
    return float(now) if now is not None and not pd.isna(now) else float("nan")


def _review_frame(sub: pd.DataFrame, include_open: bool) -> pd.DataFrame:
    """One row per trade: how it is doing, plus the technicals saved when it was bought."""
    rows = []
    for r in sub.itertuples():
        sig = paper_trades.parse_signals(r.signals)
        if pd.isna(r.sell_price):
            if not include_open:
                continue
            now = _now_price(r.symbol)
            if pd.isna(now):
                continue
            pct, status = (now / r.buy_price - 1) * 100, "open"
        else:
            pct, status = (r.sell_price / r.buy_price - 1) * 100, "closed"
        pct = round(pct, 2) + 0.0  # same rounding as the rest of the page, so float noise never counts as a win or loss
        days = ((date.fromisoformat(r.sell_date) if status == "closed" else date.today()) - date.fromisoformat(r.buy_date)).days
        rows.append({"Stock": r.symbol, "Bought": r.buy_date, "Status": status, "Days": days, "Gain/loss %": pct,
                     "Has snapshot": bool(sig and "Recommendation" in sig), **{c: sig.get(c) for c in paper_trades.SIGNAL_FIELDS}})
    return pd.DataFrame(rows)


def _first_date(series_bool: pd.Series):
    hit = series_bool[series_bool]
    return hit.index[0].date().isoformat() if len(hit) else ""


def _minervini_frame(sub: pd.DataFrame, include_open: bool) -> pd.DataFrame:
    """One row per Minervini trade: the plan it was bought with, what happened since, and whether the rules were followed."""
    hist = _history(tuple(sorted(sub["symbol"].unique())))
    rows = []
    for r in sub.itertuples():
        plan = (paper_trades.parse_signals(r.signals).get("plan") or {})
        if not plan or (pd.isna(r.sell_price) and not include_open):
            continue
        stop, t2, t3 = plan.get("Stop"), plan.get("Target 2R"), plan.get("Target 3R")
        px = r.sell_price if pd.notna(r.sell_price) else _now_price(r.symbol)
        if pd.isna(px):
            continue
        risk = r.buy_price - stop if stop else float("nan")
        h = hist.get(r.symbol)
        stop_hit = t2_hit = t3_hit = ""
        if h is not None and not h.empty:
            after = h[h.index > pd.Timestamp(r.buy_session or r.buy_date)]
            if len(after):
                lows = after["Low"].where(after["Low"].notna(), after["Close"])
                highs = after["High"].where(after["High"].notna(), after["Close"])
                stop_hit = _first_date(lows <= stop) if stop else ""
                t2_hit = _first_date(highs >= t2) if t2 else ""
                t3_hit = _first_date(highs >= t3) if t3 else ""
        triggered = plan.get("Status") == "Broke out"
        stop_ok = plan.get("Trade check") in ("OK", "OK, but stop is wide")
        market_ok = plan.get("Market state") != "Downtrend"
        pct = round((px / r.buy_price - 1) * 100, 2) + 0.0
        rows.append({
            "Stock": r.symbol, "Bought": r.buy_date, "Position": "open" if pd.isna(r.sell_price) else "closed",
            "Status at buy": plan.get("Status"), "Trade check at buy": plan.get("Trade check"), "Market at buy": plan.get("Market state"),
            "Entry": r.buy_price, "Stop": stop, "Target 2R": t2, "Risk %": plan.get("Risk %"), "Price now / sold": px, "Gain/loss %": pct,
            "R": (px - r.buy_price) / risk if risk and risk > 0 else float("nan"),
            "Stop touched": stop_hit, "2R reached": t2_hit, "3R reached": t3_hit,
            "Triggered": triggered, "Stop OK": stop_ok, "Market OK": market_ok, "Followed all rules": bool(triggered and stop_ok and market_ok),
        })
    return pd.DataFrame(rows)


def render_section(sub: pd.DataFrame, key: str, minervini: bool) -> None:
    open_df = sub[sub["sell_price"].isna()].copy()
    closed_df = sub[sub["sell_price"].notna()].copy()

    open_rows = []
    for r in open_df.itertuples():
        now = _now_price(r.symbol)
        plan = paper_trades.parse_signals(r.signals).get("plan") or {}
        open_rows.append({
            "id": r.id, "symbol": r.symbol, "sector": r.sector, "qty": r.qty, "buy_price": r.buy_price, "buy_date": r.buy_date,
            "now": now, "invested": r.buy_price * r.qty, "value": now * r.qty, "pnl": (now - r.buy_price) * r.qty,
            "pct": (now / r.buy_price - 1) * 100, "days": (date.today() - date.fromisoformat(r.buy_date)).days,
            "stop": plan.get("Stop"), "t2": plan.get("Target 2R"),
        })
    open_calc = pd.DataFrame(open_rows)

    closed_df["invested"] = closed_df["buy_price"] * closed_df["qty"]
    closed_df["pnl"] = (closed_df["sell_price"] - closed_df["buy_price"]) * closed_df["qty"]
    closed_df["pct"] = (closed_df["sell_price"] / closed_df["buy_price"] - 1) * 100
    closed_df["days"] = [(date.fromisoformat(s) - date.fromisoformat(b)).days for s, b in zip(closed_df["sell_date"], closed_df["buy_date"])]

    m1, m2, m3, m4, m5, m6 = st.columns(6)
    if open_calc.empty:
        m1.metric("Open positions", 0)
        m2.metric("Invested", "₹0")
        m3.metric("Current value", "₹0")
        m4.metric("Unrealised P/L", "₹0")
    else:
        inv, val = open_calc["invested"].sum(), open_calc["value"].sum()
        m1.metric("Open positions", len(open_calc))
        m2.metric("Invested", _money(inv))
        m3.metric("Current value", _money(val))
        m4.metric("Unrealised P/L", _money(val - inv, signed=True), _pct((val / inv - 1) * 100), delta_color="normal")
    m5.metric("Closed trades", len(closed_df))
    m6.metric("Realised P/L", _money(closed_df["pnl"].sum(), signed=True) if len(closed_df) else "₹0")

    st.subheader("Open positions")
    if open_calc.empty:
        st.info("No open positions here. Click the 🛒 on a Trend Template card." if minervini else
                "No open paper positions. Click the 🛒 on a card (Sector Scanner, Favourites or Screener), or use the Buy box above.")
    else:
        widths = [1.7, 1.1, 1.1, 0.7, 1.2, 1.1, 1.0, 1.2, 0.7, 1.0]
        head = st.columns(widths)
        for col, label in zip(head, ["Stock", "Bought on", "Buy price", "Qty", "Invested", "Now", "Gain/loss", "P/L ₹", "Days", ""]):
            col.markdown(f"**{label}**")
        for r in open_calc.sort_values("buy_date", ascending=False).itertuples():
            c = st.columns(widths, vertical_alignment="center")
            extra = ""
            if minervini and r.stop and not pd.isna(r.stop):
                below = " :red[below stop]" if not pd.isna(r.now) and r.now <= r.stop else ""
                extra = f"  \n<small>stop ₹{r.stop:,.2f} · 2R ₹{r.t2:,.2f}</small>{below}"
            c[0].markdown(f"**{r.symbol}**  \n<small>{r.sector}</small>{extra}", unsafe_allow_html=True)
            c[1].write(r.buy_date)
            c[2].write(f"₹{r.buy_price:,.2f}")
            c[3].write(f"{r.qty}")
            c[4].write(_money(r.invested))
            c[5].write("n/a" if pd.isna(r.now) else f"₹{r.now:,.2f}")
            c[6].markdown("n/a" if pd.isna(r.now) else _colour(f"**{_pct(r.pct)}**", round(r.pct, 2)))
            c[7].markdown("n/a" if pd.isna(r.now) else _colour(_money(r.pnl, signed=True), round(r.pnl)))
            c[8].write(f"{r.days}")
            c[9].button("Sell", key=f"sell_{r.id}", on_click=_sell, args=(r.id, r.symbol, r.buy_price, r.qty), disabled=pd.isna(r.now))

    st.subheader("Closed trades")
    if closed_df.empty:
        st.caption("Nothing sold yet. Press **Sell** on an open position to record the outcome.")
    else:
        show = closed_df.sort_values("sell_date", ascending=False)
        table = pd.DataFrame({
            "Stock": show["symbol"], "Sector": show["sector"], "Bought": show["buy_date"], "Sold": show["sell_date"],
            "Days held": show["days"], "Qty": show["qty"], "Buy price": show["buy_price"], "Sell price": show["sell_price"],
            "Invested ₹": show["invested"], "P/L ₹": show["pnl"], "Gain/loss %": show["pct"],
        }).reset_index(drop=True)
        st.dataframe(
            table.style.map(_tint, subset=["P/L ₹", "Gain/loss %"]),
            column_config={
                "Buy price": st.column_config.NumberColumn(format="₹%.2f"),
                "Sell price": st.column_config.NumberColumn(format="₹%.2f"),
                "Invested ₹": st.column_config.NumberColumn(format="₹%.0f"),
                "P/L ₹": st.column_config.NumberColumn(format="₹%.0f"),
                "Gain/loss %": st.column_config.NumberColumn(format="%.2f%%"),
            },
            hide_index=True, width="stretch",
        )

    scope = st.radio("Trades to include in the reviews below", ["Closed + open (open ones at today's price)", "Closed trades only"], horizontal=True, key=f"review_scope_{key}")
    include_open = scope.startswith("Closed +")

    if minervini:
        st.subheader("Minervini check - did the plan and the rules work?")
        mf = _minervini_frame(sub, include_open) if not sub.empty else pd.DataFrame()
        missing = len(sub) - len(mf) if not sub.empty else 0
        if missing and include_open:
            st.caption(f"{missing} trade(s) here have no saved plan (for example, moved here by hand) and are left out of this check.")
        if mf.empty:
            st.info("Nothing to check yet. Buy from a Trend Template card; each buy saves its pivot, stop, targets, setup status and the market trend that day.")
        else:
            a, b, c, d, e = st.columns(5)
            a.metric("Trades checked", len(mf))
            b.metric("Followed all rules", int(mf["Followed all rules"].sum()), help="Setup had broken out (not just near the pivot), the chart stop was within 10%, and the Nifty was not in a downtrend that day.")
            c.metric("Stop touched since buy", int((mf["Stop touched"] != "").sum()), help="The day's low went to or under the stop at some point after the buy (intraday).")
            d.metric("Reached 2R", int((mf["2R reached"] != "").sum()), help="The day's high reached the 2x-risk target at some point after the buy.")
            e.metric("Average R", f"{mf['R'].mean():+.2f}" if mf["R"].notna().any() else "n/a", help="R = gain divided by the risk to the stop. +1R means a gain equal to what you risked.")
            if len(mf) < 20:
                st.caption("⚠️ Fewer than about 20 trades: treat these as hints, not proof.")
            st.caption("'Stop touched' and 'Reached 2R' use daily highs and lows since the buy day. They can't tell which came first when both happen on the same day.")

            def _grp(col, label):
                g = mf.groupby(col, dropna=False).agg(Trades=("Stock", "count"), Winners=("Gain/loss %", lambda v: int((v > 0).sum())),
                                                      **{"Avg gain/loss %": ("Gain/loss %", "mean"), "Avg R": ("R", "mean")}).reset_index().rename(columns={col: label})
                return g
            t1, t2 = st.columns(2)
            with t1:
                st.markdown("**By whether all rules were followed**")
                st.dataframe(_grp("Followed all rules", "Followed all rules"), hide_index=True, width="stretch",
                             column_config={"Avg gain/loss %": st.column_config.NumberColumn(format="%.2f%%"), "Avg R": st.column_config.NumberColumn(format="%.2f")})
            with t2:
                st.markdown("**By setup status at buy**")
                st.dataframe(_grp("Status at buy", "Status at buy"), hide_index=True, width="stretch",
                             column_config={"Avg gain/loss %": st.column_config.NumberColumn(format="%.2f%%"), "Avg R": st.column_config.NumberColumn(format="%.2f")})
            st.markdown("**By market trend at buy**")
            st.dataframe(_grp("Market at buy", "Market at buy"), hide_index=True, width="stretch",
                         column_config={"Avg gain/loss %": st.column_config.NumberColumn(format="%.2f%%"), "Avg R": st.column_config.NumberColumn(format="%.2f")})
            with st.expander("Every Minervini trade with its plan and outcome"):
                st.dataframe(
                    mf.sort_values("Bought", ascending=False).style.map(_tint, subset=["Gain/loss %"]), hide_index=True, width="stretch",
                    column_config={"Entry": st.column_config.NumberColumn(format="%.2f"), "Stop": st.column_config.NumberColumn(format="%.2f"),
                                   "Target 2R": st.column_config.NumberColumn(format="%.2f"), "Risk %": st.column_config.NumberColumn(format="percent"),
                                   "Price now / sold": st.column_config.NumberColumn(format="%.2f"), "Gain/loss %": st.column_config.NumberColumn(format="%.2f%%"),
                                   "R": st.column_config.NumberColumn(format="%.2f")})

    st.subheader("Technicals at buy - did they work?")
    if sub.empty:
        st.caption("No trades yet. Once you buy, each trade's technicals are saved here for review.")
        return
    rev = _review_frame(sub, include_open)
    with_sig = rev[rev["Has snapshot"]] if not rev.empty else rev
    older = len(rev) - len(with_sig) if not rev.empty else 0
    if older:
        st.caption(f"{older} trade(s) were bought before snapshots existed and are left out of the review (their technicals were not saved).")
    if with_sig.empty:
        st.info("Nothing to review yet: no trade in this selection has saved technicals.")
        return
    win = with_sig["Gain/loss %"] > 0
    a, b, c = st.columns(3)
    a.metric("Trades reviewed", len(with_sig))
    b.metric("Winners", f"{int(win.sum())} ({win.mean():.0%})")
    c.metric("Average gain/loss", _pct(with_sig["Gain/loss %"].mean()))
    if len(with_sig) < 20:
        st.caption("⚠️ Fewer than about 20 trades: treat these comparisons as hints, not proof.")

    st.markdown("**By recommendation on the buy day**")
    by_rec = with_sig.groupby("Recommendation", dropna=False).agg(
        Trades=("Stock", "count"), Winners=("Gain/loss %", lambda v: int((v > 0).sum())),
        **{"Avg gain/loss %": ("Gain/loss %", "mean"), "Best %": ("Gain/loss %", "max"), "Worst %": ("Gain/loss %", "min")},
    ).reset_index()
    by_rec["Win rate"] = by_rec["Winners"] / by_rec["Trades"] * 100
    st.dataframe(by_rec[["Recommendation", "Trades", "Winners", "Win rate", "Avg gain/loss %", "Best %", "Worst %"]],
                 column_config={c: st.column_config.NumberColumn(format="%.1f%%") for c in ["Win rate", "Avg gain/loss %", "Best %", "Worst %"]},
                 hide_index=True, width="stretch")

    st.markdown("**Winners vs losers: average of each technical on the buy day**")
    if win.all() or (~win).all():
        st.caption("All reviewed trades are on the same side (all winners or all losers), so there is nothing to compare yet.")
    else:
        comp = []
        for col in ["Buy Score"] + SIGNAL_NUM:
            w, l = with_sig.loc[win, col].dropna(), with_sig.loc[~win, col].dropna()
            if len(w) and len(l):
                scale = 100 if col in PCT_SIGNALS else 1
                comp.append({"Technical": paper_trades.SIGNAL_FIELDS[col] + (" (%)" if col in PCT_SIGNALS else ""),
                             "Winners avg": w.mean() * scale, "Losers avg": l.mean() * scale, "Difference": (w.mean() - l.mean()) * scale})
        st.dataframe(pd.DataFrame(comp), column_config={c: st.column_config.NumberColumn(format="%.2f") for c in ["Winners avg", "Losers avg", "Difference"]},
                     hide_index=True, width="stretch")
        st.caption("A big gap means that technical looked different for winners and losers. It does not prove it caused the result.")

    with st.expander("Every trade with its technicals at buy"):
        show = with_sig.drop(columns=["Has snapshot"]).rename(columns=paper_trades.SIGNAL_FIELDS)
        for col in PCT_SIGNALS:
            show[paper_trades.SIGNAL_FIELDS[col]] = show[paper_trades.SIGNAL_FIELDS[col]] * 100
        st.dataframe(show.sort_values("Bought", ascending=False), hide_index=True, width="stretch",
                     column_config={"Gain/loss %": st.column_config.NumberColumn(format="%.2f%%"),
                                    **{paper_trades.SIGNAL_FIELDS[c]: st.column_config.NumberColumn(format="%.1f%%") for c in PCT_SIGNALS}})
        st.caption("Percent-type technicals are shown in %. 'Up/Down vol', 'Vol trend' and 'Volatility' are ratios.")


std_trades = trades[trades["section"] == paper_trades.GENERAL]
min_trades = trades[trades["section"] == paper_trades.MINERVINI]
tab_std, tab_min = st.tabs([f"🛒 Scanner & screener trades ({len(std_trades)})", f"📐 Minervini trades ({len(min_trades)})"])

with tab_std:
    # ---- new buy ----
    with st.container(border=True):
        st.subheader("Buy")
        options = sorted(price_of)
        pick = st.selectbox(
            "Stock", options, index=None, placeholder="Search a symbol...",
            format_func=lambda s: f"{s} - {company_of.get(s, '')} ({sector_of.get(s, '')})",
        )
        if pick:
            price = price_of.get(pick)
            qty = paper_trades.quantity_for(price)
            have = held.get(pick, 0)
            at_max = have >= paper_trades.MAX_POSITIONS_PER_STOCK
            if qty < 1:
                st.warning("No price available, or a single share costs more than the limit.")
            else:
                st.write(f"**{pick}** at **₹{price:,.2f}** → **{qty} shares = ₹{qty * price:,.0f}** (limit ₹{paper_trades.BUDGET:,})")
            if have:
                st.caption(f"You hold {have} of {paper_trades.MAX_POSITIONS_PER_STOCK} allowed open positions in {pick}.")
            st.button(
                f"✅ Already at the maximum ({paper_trades.MAX_POSITIONS_PER_STOCK} positions)" if at_max else "🛒 Buy",
                on_click=_buy, args=(pick,), disabled=qty < 1 or at_max, type="primary",
            )
            st.caption("Each buy also saves today's technicals for this stock, so you can review later what the signals looked like.")
    render_section(std_trades, "std", minervini=False)

with tab_min:
    st.caption(
        "Trades bought with the 🛒 on a **Trend Template** card. Each one saves the card's plan (setup status, pivot, stop, targets, market trend) so it can be checked "
        "against the rules: did it break out before the buy, was the stop within 10%, was the Nifty not in a downtrend, and did price touch the stop or reach 2R. "
        "Buy and sell rules are the same as the other section (₹50,000 per buy, last close, up to 3 positions per stock across both sections)."
    )
    render_section(min_trades, "mnv", minervini=True)

if not trades.empty:
    export = trades.copy()
    export["status"] = export["sell_price"].map(lambda v: "open" if pd.isna(v) else "closed")
    sig_cols = pd.DataFrame([{paper_trades.SIGNAL_FIELDS[k]: v for k, v in paper_trades.parse_signals(t).items() if k in paper_trades.SIGNAL_FIELDS}
                             for t in export["signals"]], index=export.index)
    plan_cols = pd.DataFrame([paper_trades.parse_signals(t).get("plan") or {} for t in export["signals"]], index=export.index)
    export = pd.concat([export.drop(columns=["signals"]), sig_cols.add_prefix("buy: "), plan_cols.add_prefix("plan: ")], axis=1)
    st.download_button("⬇️ Download all paper trades (with technicals and plans at buy) as CSV", data=export.drop(columns=["id"]).to_csv(index=False).encode("utf-8"),
                       file_name="paper_trades.csv", mime="text/csv")

    with st.expander("Made a mistake? Remove a trade, or move it to the other section"):
        labels = {r.id: f"{r.symbol} - bought {r.buy_date} at ₹{r.buy_price:,.2f}{'' if pd.isna(r.sell_price) else ' (sold)'} [{'Minervini' if r.section == paper_trades.MINERVINI else 'Scanner'}]"
                  for r in trades.itertuples()}
        chosen = st.selectbox("Trade", list(labels), index=None, format_func=labels.get, placeholder="Choose a trade...")
        c1, c2 = st.columns(2)
        if c1.button("🗑️ Delete this trade", disabled=chosen is None):
            paper_trades.delete(chosen)
            st.session_state["paper_flash"] = "Trade removed."
            st.rerun()
        if chosen is not None:
            current = trades.loc[trades["id"] == chosen, "section"].iloc[0]
            target = paper_trades.GENERAL if current == paper_trades.MINERVINI else paper_trades.MINERVINI
            if c2.button(f"↔️ Move to the {'Scanner' if target == paper_trades.GENERAL else 'Minervini'} section"):
                paper_trades.set_strategy(chosen, target)
                st.session_state["paper_flash"] = "Trade moved. (A trade moved into the Minervini section has no saved plan, so it is left out of the Minervini check.)"
                st.rerun()
