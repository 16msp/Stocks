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
        qty = paper_trades.buy(symbol, sector_of.get(symbol, ""), price, latest_session(symbol))
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
        full = have >= paper_trades.MAX_POSITIONS_PER_STOCK
        if qty < 1:
            st.warning("No price available, or a single share costs more than the limit.")
        else:
            st.write(f"**{pick}** at **₹{price:,.2f}** → **{qty} shares = ₹{qty * price:,.0f}** (limit ₹{paper_trades.BUDGET:,})")
        if have:
            st.caption(f"You hold {have} of {paper_trades.MAX_POSITIONS_PER_STOCK} allowed open positions in {pick}.")
        st.button(
            f"✅ Already at the maximum ({paper_trades.MAX_POSITIONS_PER_STOCK} positions)" if full else "🛒 Buy",
            on_click=_buy, args=(pick,), disabled=qty < 1 or full, type="primary",
        )

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

if not trades.empty:
    export = trades.copy()
    export["status"] = export["sell_price"].map(lambda v: "open" if pd.isna(v) else "closed")
    st.download_button("⬇️ Download all paper trades as CSV", data=export.drop(columns=["id"]).to_csv(index=False).encode("utf-8"),
                       file_name="paper_trades.csv", mime="text/csv")

    with st.expander("Made a mistake? Remove a trade"):
        labels = {r.id: f"{r.symbol} - bought {r.buy_date} at ₹{r.buy_price:,.2f}{'' if pd.isna(r.sell_price) else ' (sold)'}" for r in trades.itertuples()}
        chosen = st.selectbox("Trade", list(labels), index=None, format_func=labels.get, placeholder="Choose a trade...")
        if st.button("🗑️ Delete this trade", disabled=chosen is None):
            paper_trades.delete(chosen)
            st.session_state["paper_flash"] = "Trade removed."
            st.rerun()
