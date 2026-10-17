"""Streamlit page: paper-trading record. Buy from any card (🛒) or here, sell here, see gain/loss % and duration."""

import sys
from datetime import date
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.append(str(Path(__file__).resolve().parent.parent))
from strategies import paper_trades  # noqa: E402
from strategies.scan_all import scan_all_sectors  # noqa: E402
from strategies.scan_ui import latest_session  # noqa: E402

st.set_page_config(page_title="Paper Trades", page_icon="🛒", layout="wide")

from strategies.auth_gate import require_password  # noqa: E402

require_password()
st.title("🛒 Paper Trades")
st.caption(
    f"Practice trading with no real money. Each buy spends at most **₹{paper_trades.BUDGET:,}** (shares are worked out for you), "
    "and you can hold up to **3 positions per stock**, and each sell closes one whole position. Trades happen at the **last completed daily close**, with no brokerage, taxes or "
    "slippage, so real results will be a little worse. Buy with the 🛒 on any card in the Sector Scanner, Favourites or Screener, "
    "or from the box below."
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

# ---- open positions ----
open_df = trades[trades["sell_price"].isna()].copy()
closed_df = trades[trades["sell_price"].notna()].copy()

open_rows = []
for r in open_df.itertuples():
    now = price_of.get(r.symbol)
    now = float(now) if now is not None and not pd.isna(now) else float("nan")
    invested = r.buy_price * r.qty
    open_rows.append({
        "id": r.id, "symbol": r.symbol, "sector": r.sector, "qty": r.qty, "buy_price": r.buy_price, "buy_date": r.buy_date,
        "now": now, "invested": invested, "value": now * r.qty, "pnl": (now - r.buy_price) * r.qty,
        "pct": (now / r.buy_price - 1) * 100, "days": (date.today() - date.fromisoformat(r.buy_date)).days,
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
    st.info("No open paper positions. Click the 🛒 on a card (Sector Scanner, Favourites or Screener), or use the Buy box above.")
else:
    widths = [1.5, 1.1, 1.1, 0.7, 1.2, 1.1, 1.0, 1.2, 0.7, 1.0]
    head = st.columns(widths)
    for col, label in zip(head, ["Stock", "Bought on", "Buy price", "Qty", "Invested", "Now", "Gain/loss", "P/L ₹", "Days", ""]):
        col.markdown(f"**{label}**")
    for r in open_calc.sort_values("buy_date", ascending=False).itertuples():
        c = st.columns(widths, vertical_alignment="center")
        c[0].markdown(f"**{r.symbol}**  \n<small>{r.sector}</small>", unsafe_allow_html=True)
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

    def _tint(v):
        if pd.isna(v) or v == 0:
            return ""
        return "background-color: #c6efce; color: #006100" if v > 0 else "background-color: #ffc7ce; color: #9c0006"

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

# ---- did the technicals work? ----
st.subheader("Technicals at buy - did they work?")
SIGNAL_NUM = [c for c in paper_trades.SIGNAL_FIELDS if c not in paper_trades.TEXT_SIGNALS and c != "Buy Score"]
PCT_SIGNALS = {"52W High Drop", "Off 52W Low", "RS 1M", "RS 3M", "vs 200DMA", "Overhead Supply", "Supply to +25%", "vs POC", "Worst 1D Move"}


def _review_frame(include_open: bool) -> pd.DataFrame:
    """One row per trade: how it is doing, plus the technicals saved when it was bought."""
    rows = []
    for r in trades.itertuples():
        sig = paper_trades.parse_signals(r.signals)
        if pd.isna(r.sell_price):
            if not include_open:
                continue
            now = price_of.get(r.symbol)
            if now is None or pd.isna(now):
                continue
            pct, status = (float(now) / r.buy_price - 1) * 100, "open"
        else:
            pct, status = (r.sell_price / r.buy_price - 1) * 100, "closed"
        pct = round(pct, 2) + 0.0  # same rounding as the rest of the page, so float noise never counts as a win or loss
        days = ((date.fromisoformat(r.sell_date) if status == "closed" else date.today()) - date.fromisoformat(r.buy_date)).days
        rows.append({"Stock": r.symbol, "Bought": r.buy_date, "Status": status, "Days": days, "Gain/loss %": pct,
                     "Has snapshot": bool(sig), **{c: sig.get(c) for c in paper_trades.SIGNAL_FIELDS}})
    return pd.DataFrame(rows)


if trades.empty:
    st.caption("No trades yet. Once you buy, each trade's technicals are saved here for review.")
else:
    scope = st.radio("Trades to include", ["Closed + open (open ones at today's price)", "Closed trades only"], horizontal=True, key="review_scope")
    rev = _review_frame(include_open=scope.startswith("Closed +"))
    with_sig = rev[rev["Has snapshot"]] if not rev.empty else rev
    older = len(rev) - len(with_sig) if not rev.empty else 0
    if older:
        st.caption(f"{older} trade(s) were bought before snapshots existed and are left out of the review (their technicals were not saved).")
    if with_sig.empty:
        st.info("Nothing to review yet: no trade in this selection has saved technicals.")
    else:
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

if not trades.empty:
    export = trades.copy()
    export["status"] = export["sell_price"].map(lambda v: "open" if pd.isna(v) else "closed")
    sig_cols = pd.DataFrame([{paper_trades.SIGNAL_FIELDS[k]: v for k, v in paper_trades.parse_signals(t).items() if k in paper_trades.SIGNAL_FIELDS}
                             for t in export["signals"]], index=export.index)
    export = pd.concat([export.drop(columns=["signals"]), sig_cols.add_prefix("buy: ")], axis=1)
    st.download_button("⬇️ Download all paper trades (with technicals at buy) as CSV", data=export.drop(columns=["id"]).to_csv(index=False).encode("utf-8"),
                       file_name="paper_trades.csv", mime="text/csv")

    with st.expander("Made a mistake? Remove a trade"):
        labels = {r.id: f"{r.symbol} - bought {r.buy_date} at ₹{r.buy_price:,.2f}{'' if pd.isna(r.sell_price) else ' (sold)'}" for r in trades.itertuples()}
        chosen = st.selectbox("Trade", list(labels), index=None, format_func=labels.get, placeholder="Choose a trade...")
        if st.button("🗑️ Delete this trade", disabled=chosen is None):
            paper_trades.delete(chosen)
            st.session_state["paper_flash"] = "Trade removed."
            st.rerun()
