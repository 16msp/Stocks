"""Shared pieces of the sector pages: links, number formatting, coloured chips, the stock card and the favourite toggle."""

from __future__ import annotations

from urllib.parse import quote

import pandas as pd
import streamlit as st

from strategies import favourites, paper_trades, price_store
from strategies.holdings_style import BAD, GOOD, MID

FAV_ON, FAV_OFF = "❤️", "🤍"


def favourite_symbols(refresh: bool = False) -> set:
    """Favourite tickers, loaded once per session (cards must not query the database each)."""
    if refresh or "fav_symbols" not in st.session_state:
        try:
            frame = favourites.load()
            st.session_state["fav_symbols"] = set(frame["Stock"])
            st.session_state["fav_error"] = ""
        except Exception as exc:  # database unreachable: keep the page usable
            st.session_state.setdefault("fav_symbols", set())
            st.session_state["fav_error"] = str(exc)[:200]
    return st.session_state["fav_symbols"]


def _toggle_favourite(symbol: str, sector: str) -> None:
    favs = favourite_symbols()
    try:
        if symbol in favs:
            favourites.remove([symbol])
            favs.discard(symbol)
        else:
            favourites.add(symbol, sector)
            favs.add(symbol)
    except Exception as exc:
        st.toast(f"Could not update favourites: {str(exc)[:120]}", icon="⚠️")


def paper_open_counts(refresh: bool = False) -> dict:
    """{ticker: open paper positions}, loaded once per session."""
    if refresh or "paper_open" not in st.session_state:
        try:
            st.session_state["paper_open"] = paper_trades.open_counts()
        except Exception:
            st.session_state.setdefault("paper_open", {})
    return st.session_state["paper_open"]


def latest_session(symbol: str):
    """Date of the last stored daily bar (the price a paper trade executes at)."""
    try:
        return price_store.get_status([symbol]).get("latest_session")
    except Exception:
        return None


def _paper_buy(symbol: str, sector: str, price: float, signals: dict | None = None) -> None:
    try:
        qty = paper_trades.buy(symbol, sector, price, latest_session(symbol), signals)
        counts = paper_open_counts()
        counts[symbol] = counts.get(symbol, 0) + 1
        st.toast(f"Paper bought {qty} x {symbol} at ₹{price:,.2f} = ₹{qty * price:,.0f}. See the Paper Trades page.", icon="🛒")
    except Exception as exc:
        st.toast(f"Could not record the paper buy: {str(exc)[:140]}", icon="⚠️")


PLAN_ICON = {"Broke out": "🟢", "Near pivot": "🟡", "Building": "⚪", "Extended": "🔴", "No data": "⚪"}
REC_ICON = {"Accumulate": "🟢", "Strong - wait for dip": "🟡", "Watch - base forming": "🟡", "Avoid for now": "🔴", "Price break - check": "⚠️", "Insufficient data": "⚪"}


def tradingview_url(symbol):
    return f"https://www.tradingview.com/chart/?symbol=NSE%3A{quote(symbol, safe='')}"


def screener_url(symbol):
    return f"https://www.screener.in/company/{quote(symbol, safe='')}/"


def _fmt(v, kind):
    if v is None or pd.isna(v):
        return "n/a"
    return {"pct": f"{v:+.1%}", "pct0": f"{v:.0%}", "x": f"{v:.2f}x", "int": f"{v:.0f}", "px": f"{v:,.2f}"}[kind]


def _chip(label, value, style):
    """Coloured inline chip: label on top-line, value in a tinted box."""
    return f'<span style="{style};padding:1px 6px;border-radius:6px;margin-right:4px;white-space:nowrap;font-size:0.85em">{label}: <b>{value}</b></span>'


def _style_for(value, good, bad, higher=True):
    if value is None or pd.isna(value):
        return ""
    v, g, b = (value, good, bad) if higher else (-value, -good, -bad)
    return GOOD if v >= g else (BAD if v <= b else MID)


def render_card(r, sector: str = "", key_prefix: str = "card", plan: dict | None = None):
    """One stock card. With `plan` (from strategies.entry_plan) the card shows the Trend Template view: setup status, RS rating,
    template pass/fail reasons (r["Template why"]) and a buy point / stop / risk block."""
    with st.container(border=True):
        head, body, why = st.columns([1.5, 3.0, 2.0])
        with head:
            owned = f" &nbsp; {r['Owned']}" if r["Owned"] else ""
            pick = f"{r['Pick']} &nbsp; " if r["Pick"] else ""
            title, heart, cart = st.columns([3, 1, 1], vertical_alignment="center")
            title.markdown(f"### {pick}{r['Stock']}{owned}")
            is_fav = r["Stock"] in favourite_symbols()
            heart.button(
                FAV_ON if is_fav else FAV_OFF,
                key=f"{key_prefix}_fav_{r['Stock']}",
                help="Remove from favourites" if is_fav else "Add to favourites (see them all on the Favourites page)",
                on_click=_toggle_favourite,
                args=(r["Stock"], sector or r.get("Sector", "")),
            )
            price = r["CMP"]
            qty = paper_trades.quantity_for(price)
            held = paper_open_counts().get(r["Stock"], 0)
            full = held >= paper_trades.MAX_POSITIONS_PER_STOCK
            if full:
                tip = f"You hold {held} of {paper_trades.MAX_POSITIONS_PER_STOCK} allowed paper positions - sell one on the Paper Trades page to buy again"
            elif qty >= 1:
                tip = f"Paper buy {qty} shares at ₹{price:,.2f} = ₹{qty * price:,.0f} (limit ₹{paper_trades.BUDGET:,})"
                if held:
                    tip += f". You already hold {held} of {paper_trades.MAX_POSITIONS_PER_STOCK}."
            else:
                tip = "No price, or one share costs more than the paper-trade limit"
            cart.button(
                "✅" if full else (f"🛒{held}" if held else "🛒"),
                key=f"{key_prefix}_buy_{r['Stock']}",
                disabled=full or qty < 1,
                help=tip,
                on_click=_paper_buy,
                args=(r["Stock"], sector or r.get("Sector", ""), price, paper_trades.snapshot(r)),
            )
            st.caption(r["Company"])
            st.markdown(f"[📈 TradingView]({tradingview_url(r['Stock'])}) &nbsp;·&nbsp; [📊 Screener]({screener_url(r['Stock'])})")
            if plan:
                st.markdown(f"{PLAN_ICON.get(plan['Status'], '')} **{plan['Status']}**")
                st.metric("RS Rating", _fmt(r["RS Rating"], "int"), help="1-99 rank of the 3/6/9/12-month return against the stocks in these sector lists")
            else:
                st.markdown(f"{REC_ICON.get(r['Recommendation'], '')} **{r['Recommendation']}**")
                st.metric("Buy Score", _fmt(r["Buy Score"], "int"), help="0-100 timing score")
        with body:
            st.markdown(
                f"**CMP ₹{_fmt(r['CMP'], 'px')}** &nbsp;|&nbsp; 52W high drop **{_fmt(r['52W High Drop'], 'pct0')}** "
                f"&nbsp;|&nbsp; bounce off low **{_fmt(r['Off 52W Low'], 'pct0')}** &nbsp;|&nbsp; RSI **{_fmt(r['RSI 14'], 'int')}**"
            )
            st.markdown(
                _chip("RS 1M", _fmt(r["RS 1M"], "pct"), _style_for(r["RS 1M"], 0.02, -0.02))
                + _chip("RS 3M", _fmt(r["RS 3M"], "pct"), _style_for(r["RS 3M"], 0.02, -0.02))
                + _chip("vs 200DMA", _fmt(r["vs 200DMA"], "pct"), _style_for(r["vs 200DMA"], 0.0, -0.10)),
                unsafe_allow_html=True,
            )
            st.markdown(
                _chip("Up/Down vol", _fmt(r["Up/Down Vol"], "x"), _style_for(r["Up/Down Vol"], 1.2, 0.8))
                + _chip("Vol trend", _fmt(r["Vol Trend"], "x"), _style_for(r["Vol Trend"], 1.2, 0.7))
                + _chip("Days since low", _fmt(r["Days Since Low"], "int"), _style_for(r["Days Since Low"], 25, 10)),
                unsafe_allow_html=True,
            )
            st.markdown(
                _chip("Overhead supply", _fmt(r["Overhead Supply"], "pct0"), _style_for(r["Overhead Supply"], 0.30, 0.70, higher=False))
                + _chip("Supply to +25%", _fmt(r["Supply to +25%"], "pct0"), _style_for(r["Supply to +25%"], 0.40, 0.70, higher=False))
                + _chip("Volatility", _fmt(r["Volatility Ratio"], "x"), _style_for(r["Volatility Ratio"], 0.85, 1.15, higher=False)),
                unsafe_allow_html=True,
            )
            st.caption(f"Volume-profile zone: {r['VP Zone'] or 'n/a'} (CMP {_fmt(r['vs POC'], 'pct')} vs POC) · avg traded ₹{_fmt(r['Avg Value 20D'], 'px')} cr/day")
        with why:
            why_text = r["Template why"] if plan else r["Why"]
            good, _, bad = why_text.partition(" | ")
            if not why_text.startswith("✔"):
                good, bad = "", why_text
            for item in [g for g in good.removeprefix("✔ ").split("; ") if g]:
                st.markdown(f"✔ {item}")
            for item in [b for b in bad.removeprefix("✖ ").split("; ") if b]:
                st.markdown(f"✖ {item}")
        if plan:
            _plan_block(r, plan)


def _plan_block(r, plan: dict) -> None:
    st.divider()
    st.markdown(f"**Trade plan:** {plan['Verdict']}")
    if pd.isna(plan["Entry"]):
        return
    entry, stop = plan["Entry"], plan["Stop"]
    qty = paper_trades.quantity_for(entry)
    a, b, c, d = st.columns(4)
    a.metric("Pivot (buy point)", f"₹{plan['Pivot']:,.2f}", f"{plan['To pivot']:+.1%} from the pivot", delta_color="off")
    b.metric("Entry / buy up to", f"₹{entry:,.2f}", f"up to ₹{plan['Buy up to']:,.2f}", delta_color="off")
    c.metric("Stop", f"₹{stop:,.2f}", f"-{plan['Risk %']:.1%} from entry", delta_color="off", help=plan["Stop note"])
    d.metric("Targets (2x / 3x the risk)", f"₹{plan['Target 2R']:,.2f}", f"3x: ₹{plan['Target 3R']:,.2f}", delta_color="off")
    loss = f" · if stopped on a ₹{paper_trades.BUDGET:,} buy ({qty} shares) you lose about ₹{qty * (entry - stop):,.0f}" if qty >= 1 else ""
    vol_line = f" · breakout volume {plan['Breakout volume']}" if plan["Breakout volume"] else f" · needs {plan['Volume to confirm']:,.0f}+ shares on the breakout day (1.4x average)"
    st.caption(
        f"Pullbacks in the base: {plan['Contractions'] or 'none of 3%+'} ({'tightening ✔' if plan['Tightening'] else 'not clearly tightening ✖'}) · "
        f"volume {'dried up ✔' if plan['Volume dried up'] else 'has not dried up ✖'}{vol_line}{loss}"
    )
