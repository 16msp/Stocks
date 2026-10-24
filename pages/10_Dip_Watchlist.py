"""Streamlit page: a hand-picked watchlist of good, growth-oriented stocks tracked by how far each is below its all-time closing high,
and a scan of every stored stock for the ones that have stopped falling and turned."""

import sys
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

sys.path.append(str(Path(__file__).resolve().parent.parent))
from strategies import dip_watchlist as dw  # noqa: E402
from strategies import price_store  # noqa: E402
from strategies.scan_all import scan_all_sectors  # noqa: E402
from strategies.scan_ui import screener_url, tradingview_url  # noqa: E402

st.set_page_config(page_title="Dip Watchlist", page_icon="🎯", layout="wide")

from strategies.auth_gate import require_password  # noqa: E402

require_password()
st.title("🎯 Dip Watchlist")
st.caption(
    "A short list of good, growth-oriented businesses that you choose, tracked by how far each is below its all-time closing high. "
    "The idea is to stay with a stock you believe in, add to it when it is far below its high, and sell as it recovers. "
    "The page only times the entry: picking a good business is your job (check the fundamentals first). "
    "A stock that really broke can stay down for years. Backtest results are past results, not a forecast. Not investment advice."
)

if st.session_state.get("dw_flash"):
    st.success(st.session_state.pop("dw_flash"))

full, _summaries = scan_all_sectors(0)
one = full.drop_duplicates("Stock").set_index("Stock")
company_of, sector_of = one["Company"].to_dict(), one["Sector"].to_dict()
SYMBOLS = tuple(sorted(company_of))

st.sidebar.header("Dip Watchlist")
if st.sidebar.button("🔄 Recalculate", width="stretch"):
    st.cache_data.clear()
    st.rerun()
st.sidebar.markdown("**Zones (below the all-time high)**")
for start, name, meaning in dw.ZONES[::-1]:
    st.sidebar.caption(f"**{name}** from {start:.0%}: {meaning}")
st.sidebar.markdown("**Turn signal (has it stopped falling?)**")
for name, meaning in dw.STAGES:
    st.sidebar.caption(f"**{name}**: {meaning}")

try:
    wl = dw.load()
except Exception as exc:
    st.error(f"Could not read the watchlist: {exc}")
    st.stop()

_GREEN2, _GREEN, _LGREEN = "background-color: #a9d18e; color: #1e4620", "background-color: #c6efce; color: #006100", "background-color: #e2f0d9; color: #375623"
_YELLOW, _ORANGE, _RED = "background-color: #fff2cc; color: #7f6000", "background-color: #fce4d6; color: #843c0c", "background-color: #ffc7ce; color: #9c0006"
_ZONE = {"Very deep": _GREEN, "Deep buy zone": _GREEN, "Buy zone": _LGREEN, "Watch": _YELLOW}
_STAGE = {"Still falling": _RED, "Stabilising": _ORANGE, "Base built": _YELLOW, "Turning early": _LGREEN, "Turning": _GREEN, "Confirmed turn": _GREEN2}


def _styles(df: pd.DataFrame) -> pd.DataFrame:
    """Cell colours: green = what the backtest liked, red = what it did not."""
    out = pd.DataFrame("", index=df.index, columns=df.columns)
    out["Zone"] = df["Zone"].map(_ZONE).fillna("")
    out["Turn signal"] = df["Turn signal"].map(_STAGE).fillna("")
    out["Days since low"] = [("" if pd.isna(d) else _RED if d < dw.FALLING_SESSIONS else _ORANGE if d < dw.BASE_SESSIONS else _GREEN) for d in df["Days since low"]]
    out["Off 52W low"] = [("" if pd.isna(v) else _GREEN if v >= 0.5 else _LGREEN if v >= 0.3 else "") for v in df["Off 52W low"]]
    out["vs 200DMA"] = [("" if pd.isna(v) else _GREEN if v > 0 else _RED) for v in df["vs 200DMA"]]
    out["RS 6M"] = [("" if pd.isna(v) else _GREEN if v > 0 else _RED) for v in df["RS 6M"]]
    # Rising volume only counts once the price is above the 200-day average; below it, it can just be selling.
    out["Volume ratio"] = [("" if pd.isna(v) else (_GREEN2 if above else _YELLOW) if v >= dw.VOLUME_RISING else _ORANGE if v < 0.7 else "")
                           for v, above in zip(df["Volume ratio"], df["vs 200DMA"].fillna(-1) > 0)]
    return out


COLUMN_CONFIG = {
    "TradingView": st.column_config.LinkColumn("TradingView", display_text="📈 Chart"),
    "Screener": st.column_config.LinkColumn("Screener", display_text="📊 Screener"),
    "Drop from ATH": st.column_config.NumberColumn(format="percent", help="Last close against the all-time closing high (since listing). Yahoo does not adjust for demergers, so a demerged company can show a bigger fall than a holder actually lost."),
    "Drop 1W ago": st.column_config.NumberColumn(format="percent", help="The same measure 5 sessions ago, to see whether the fall is still going."),
    "CMP": st.column_config.NumberColumn(format="%.2f"),
    "ATH": st.column_config.NumberColumn("All-time high", format="%.2f"),
    "ATH date": st.column_config.DateColumn("High on", format="DD MMM YYYY"),
    "Turn signal": st.column_config.TextColumn(help="Has the stock stopped falling and turned? Still falling, Stabilising, Base built, Turning early, Turning, Confirmed turn. See the legend in the sidebar."),
    "Days since low": st.column_config.NumberColumn("Days since 52W low", format="%d", help="Sessions since the lowest close of the last year. Red under 20 (still making new lows), orange under 120, green from 120 (about 6 months without a new low)."),
    "Off 52W low": st.column_config.NumberColumn(format="percent", help="How far the price has come up from its lowest close of the last year. Green from 50%, light green from 30%."),
    "vs 200DMA": st.column_config.NumberColumn("vs 200DMA", format="percent", help="Price against its 200-day average. Green above, red below."),
    "Volume ratio": st.column_config.NumberColumn("Volume 50d/250d", format="%.2fx", help="Average volume of the last 50 sessions against the last 250. Dark green: 1.2x or more with the price above the 200-day average (the case the backtest liked). Yellow: 1.2x or more but still below the 200-day average (could be selling). Orange: below 0.7x (volume dried up)."),
    "RS 6M": st.column_config.NumberColumn("6M vs Nifty", format="percent", help="Price change over the last 126 sessions minus Nifty's. Green = beating the index."),
    "Value Cr": st.column_config.NumberColumn("Value ₹ cr/day", format="%.1f", help="Median traded value of the last 20 sessions. The backtest needed at least 1 crore a day."),
}


@st.cache_data(ttl=300, show_spinner="Reading the stored prices...")
def _load(symbols: tuple) -> tuple:
    return price_store.load_history(list(symbols) + ["^NSEI"]), price_store.load_ath(list(symbols))


def _links(df: pd.DataFrame) -> pd.DataFrame:
    df.insert(2, "TradingView", df["Stock"].map(tradingview_url))
    df.insert(3, "Screener", df["Stock"].map(screener_url))
    return df


def _status_table(symbols: list, hist: dict, aths: dict) -> pd.DataFrame:
    nifty = hist["^NSEI"]["Close"] if "^NSEI" in hist else None
    rows = []
    for sym in symbols:
        s = dw.status(hist.get(sym), aths.get(sym), nifty)
        rows.append({"Stock": sym, **(s or {})})
    return pd.DataFrame(rows)


# ================================================================ my watchlist
def _add() -> None:
    picked = st.session_state.get("dw_pick", [])
    try:
        n = dw.add(picked)
        st.session_state["dw_flash"] = f"Added {n} stock(s) to the watchlist." + (f" {len(picked) - n} already on it." if len(picked) > n else "")
        st.session_state["dw_pick"] = []
    except Exception as exc:
        st.session_state["dw_flash"] = f"Could not add: {exc}"


def _watchlist_tab() -> None:
    with st.container(border=True):
        on_list = set(wl["Stock"])
        options = sorted(s for s in company_of if s not in on_list)
        c1, c2 = st.columns([5, 1], vertical_alignment="bottom")
        c1.multiselect("Add stocks to the watchlist", options, key="dw_pick", placeholder="Type a symbol or company name",
                       format_func=lambda s: f"{s} - {company_of.get(s) or ''}".rstrip(" -"),
                       help="Any stock in the app's sector lists (the ones whose prices are stored daily). The Scan tab finds candidates.")
        c2.button("➕ Add", on_click=_add, width="stretch", disabled=not st.session_state.get("dw_pick"))

    if wl.empty:
        st.info("The watchlist is empty. Add the good, growth-oriented stocks you would like to buy on a deep fall, or look for candidates in the **Scan all stocks** tab.")
        return

    hist, aths = _load(tuple(sorted(wl["Stock"])))
    t = _status_table(list(wl["Stock"]), hist, aths).merge(wl, on="Stock", how="left")
    t["Company"] = t["Stock"].map(company_of).fillna("")
    t["Sector"] = t["Stock"].map(sector_of).fillna("")
    no_price = t[t["CMP"].isna()]["Stock"].tolist()
    t = t[t["CMP"].notna()].sort_values("Drop from ATH", ascending=False).reset_index(drop=True)

    store = price_store.get_status(list(wl["Stock"]))
    if store:
        st.caption(f"💾 Prices stored in {store['backend']}, latest session {t['Last session'].max() if not t.empty else store['latest_session']}.")
    if no_price:
        st.warning(f"No stored prices yet for: {', '.join(no_price)}. They appear after the next daily price job.")
    if t.empty:
        return
    guess = t[~t["ath_known"].fillna(False).astype(bool)]["Stock"].tolist()
    if guess:
        st.caption(f"ℹ️ The all-time high of {', '.join(guess)} is not stored yet (it is refreshed weekly), so the highest stored close (about 15 months) is used for now.")

    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("On the watchlist", len(wl))
    m2.metric("🟢 In a buy zone (35%+ below)", int((t["Drop from ATH"] >= 0.35).sum()))
    m3.metric("🟠 Watching (25-35% below)", int(t["Drop from ATH"].between(0.25, 0.35, inclusive="left").sum()))
    m4.metric("⚪ Within 25% of the high", int((t["Drop from ATH"] < 0.25).sum()))
    m5.metric("🚀 Turning (35%+ below)", int(((t["Drop from ATH"] >= 0.35) & t["Turn signal"].isin(["Turning", "Confirmed turn"])).sum()),
              help="Stocks 35% or more below their high that have stopped making new lows for 6 months and are back above the 200-day average.")

    view = _links(t[["Stock", "Company", "Zone", "Turn signal", "Drop from ATH", "Drop 1W ago", "Days since low", "Off 52W low", "vs 200DMA", "Volume ratio", "RS 6M",
                     "CMP", "ATH", "ATH date", "Sector", "Note"]].copy())
    view["Drop from ATH"] = -view["Drop from ATH"]
    view["Drop 1W ago"] = -view["Drop 1W ago"]
    view["Remove"] = False

    st.caption("Sorted by the deepest fall first. Green cells are what the backtest liked (no new low for 6 months, above the 200-day average, rising volume above it); red is what it did not. "
               "Type in **Note** (why you own it, the fundamentals you checked), tick **Remove** to drop a stock, then press Save.")
    edited = st.data_editor(
        view.style.apply(_styles, axis=None),
        column_config={**COLUMN_CONFIG,
                       "Note": st.column_config.TextColumn(width="large", help="Your own reminder, saved when you press Save"),
                       "Remove": st.column_config.CheckboxColumn(help="Tick and press Save to take it off the watchlist")},
        disabled=[c for c in view.columns if c not in ("Note", "Remove")],
        hide_index=True,
        width="stretch",
        height=35 * (len(view) + 1) + 3,
        key="dw_editor",
    )

    if st.button("💾 Save notes and removals", type="primary"):
        old = dict(zip(wl["Stock"], wl["Note"].fillna("")))
        drop = [r.Stock for r in edited.itertuples() if r.Remove]
        changed = {r.Stock: r.Note for r in edited.itertuples() if (r.Note or "") != old.get(r.Stock, "") and r.Stock not in drop}
        try:
            dw.set_notes(changed)
            dw.remove(drop)
            st.session_state["dw_flash"] = f"Saved {len(changed)} note change(s), removed {len(drop)} stock(s)."
            st.rerun()
        except Exception as exc:
            st.error(f"Could not save: {exc}")

    st.subheader("Price against the all-time high")
    pick = st.selectbox("Stock", list(t["Stock"]), format_func=lambda s: f"{s} - {company_of.get(s) or ''}".rstrip(" -"))
    row = t[t["Stock"] == pick].iloc[0]
    close = hist[pick]["Close"].dropna()
    fig = go.Figure(go.Scatter(x=close.index, y=close.values, mode="lines", name="Close", line=dict(width=2)))
    dma = close.rolling(200).mean().dropna()
    if not dma.empty:
        fig.add_trace(go.Scatter(x=dma.index, y=dma.values, mode="lines", name="200-day average", line=dict(width=1.5, color="#7f7f7f", dash="dash")))
    for pct, colour in [(0, "#2e7d32"), (0.35, "#b7950b"), (0.45, "#d35400"), (0.55, "#c0392b")]:
        fig.add_hline(y=row["ATH"] * (1 - pct), line=dict(color=colour, dash="dot" if pct else "solid", width=1),
                      annotation_text="all-time high" if pct == 0 else f"{pct:.0%} below", annotation_position="top left")
    fig.update_layout(height=380, margin=dict(l=0, r=0, t=10, b=0), showlegend=True, legend=dict(orientation="h", y=1.08), yaxis_title="₹")
    st.plotly_chart(fig, use_container_width=True)
    st.caption(f"{pick}: {row['Drop from ATH']:.1%} below its all-time high of ₹{row['ATH']:,.2f} ({row['ATH date']:%d %b %Y}); zone **{row['Zone']}**, turn signal **{row['Turn signal']}**. "
               "The chart shows the stored sessions only (about 15 months).")


# ================================================================ scan all stocks
@st.cache_data(ttl=600, show_spinner="Checking every stock's last year of prices (about 15 seconds the first time)...")
def _scan_all(symbols: tuple) -> pd.DataFrame:
    hist, aths = _load(symbols)
    return _status_table(list(symbols), hist, aths)


def _add_scanned(symbols: list) -> None:
    try:
        n = dw.add(symbols)
        st.session_state["dw_flash"] = f"Added {n} stock(s) to the watchlist." + (f" {len(symbols) - n} already on it." if len(symbols) > n else "")
    except Exception as exc:
        st.session_state["dw_flash"] = f"Could not add: {exc}"


def _scan_tab() -> None:
    st.caption(
        "Every stock in the app's sector lists, checked against the rules: far below the all-time high, no new low for months, back above the 200-day average. "
        "The defaults match the backtest group that did best (70%+ below the high, **Turning** or **Confirmed turn**). Lower the minimum fall to see more."
    )
    f1, f2, f3 = st.columns([1, 2, 1])
    min_drop = f1.slider("At least this far below the all-time high", 30, 90, 70, step=5, format="%d%%", key="dw_scan_drop") / 100
    stages = f2.multiselect("Turn signal", [n for n, _ in dw.STAGES], default=["Turning", "Confirmed turn"], key="dw_scan_stage",
                            help="Turning = no new low for 6 months and above the 200-day average. Confirmed turn adds rising volume. Add 'Turning early' or 'Base built' to see stocks that are close.")
    min_value = f3.number_input("Minimum traded value (₹ cr/day)", min_value=0.0, max_value=100.0, value=1.0, step=0.5, key="dw_scan_value",
                                help="The backtest used 1 crore a day. Thinner stocks are hard to buy and sell.")
    hide_jumps = st.checkbox("Leave out stocks with a one-day move below -35% or above +60% in the last year (demergers, splits and bad prints, as the backtest did)", value=True, key="dw_scan_jumps")

    res = _scan_all(SYMBOLS)
    res = res[res["CMP"].notna()].copy()
    res["Company"] = res["Stock"].map(company_of).fillna("")
    res["Sector"] = res["Stock"].map(sector_of).fillna("")
    on_list = set(wl["Stock"])

    base = res[(res["Drop from ATH"] >= min_drop) & (res["Value Cr"].fillna(0) >= min_value)]
    if hide_jumps:
        base = base[~base["Big move"].fillna(False).astype(bool)]
    latest = res["Last session"].max()
    st.caption(f"💾 {len(res)} of {len(SYMBOLS)} stocks have stored prices, latest session {latest}. "
               f"{len(base)} are {min_drop:.0%}+ below their high and pass the volume rule; their turn signals:")
    counts = base["Turn signal"].value_counts()
    cols = st.columns(len(dw.STAGES))
    for c, (name, _) in zip(cols, dw.STAGES):
        c.metric(name, int(counts.get(name, 0)))

    hit = base[base["Turn signal"].isin(stages)].sort_values(["Drop from ATH"], ascending=False).reset_index(drop=True)
    unknown = int((~res["ath_known"].fillna(False).astype(bool)).sum())
    if unknown:
        st.caption(f"ℹ️ {unknown} stocks have no stored all-time high yet (refreshed weekly); for them the highest stored close (about 15 months) is used, so their fall may be understated.")
    if hit.empty:
        st.info("No stock meets these rules right now. Lower the minimum fall or add 'Base built' / 'Turning early' to the turn signal.")
        return

    view = _links(hit[["Stock", "Company", "Zone", "Turn signal", "Drop from ATH", "Days since low", "Off 52W low", "vs 200DMA", "Volume ratio", "RS 6M",
                       "Value Cr", "CMP", "ATH", "ATH date", "Sector"]].copy())
    view["Drop from ATH"] = -view["Drop from ATH"]
    view.insert(len(view.columns), "On watchlist", view["Stock"].isin(on_list))
    view.insert(len(view.columns), "Add", False)
    st.markdown(f"**{len(view)} stock(s) meet the rules**, deepest fall first. Tick **Add** and press the button to put them on your watchlist.")
    edited = st.data_editor(
        view.style.apply(_styles, axis=None),
        column_config={**COLUMN_CONFIG,
                       "On watchlist": st.column_config.CheckboxColumn(help="Already on your watchlist"),
                       "Add": st.column_config.CheckboxColumn(help="Tick the stocks to add, then press the button below")},
        disabled=[c for c in view.columns if c != "Add"],
        hide_index=True,
        width="stretch",
        height=min(35 * (len(view) + 1) + 3, 700),
        key="dw_scan_editor",
    )
    chosen = [r.Stock for r in edited.itertuples() if r.Add and r.Stock not in on_list]
    if st.button(f"➕ Add {len(chosen)} ticked stock(s) to the watchlist", type="primary", disabled=not chosen):
        _add_scanned(chosen)
        st.rerun()
    st.download_button("⬇️ Download these as CSV", data=view.drop(columns=["Add", "TradingView", "Screener"]).to_csv(index=False).encode("utf-8"),
                       file_name="dip_watchlist_scan.csv", mime="text/csv")
    st.caption("A turn signal says the price has stopped falling, not that the business is sound. Check why it fell and the fundamentals before adding it.")


# ================================================================ backtest
def _backtest() -> None:
    with st.expander("What the backtest says about buying at each level"):
        st.markdown(
            "Every time a stock trading at least ₹25 crore a day fell through each level below its all-time closing high (781 NSE stocks, 2015 to Oct 2026), "
            "I looked at what happened next, buying at the next open and counting costs. **Deeper was better**: the typical stock bought at 25% below did no better "
            "than any other stock, while 45% and 55% below paid more, though not every stock recovers."
        )
        bt = pd.DataFrame(dw.BACKTEST_ROWS)
        st.dataframe(bt, hide_index=True, width="stretch", column_config={
            "Median after 12 months": st.column_config.NumberColumn(format="%.1f%%"), "Median after 24 months": st.column_config.NumberColumn(format="%.1f%%"),
            "Positive after 24 months": st.column_config.NumberColumn(format="%d%%"), "Back to the old high within 3 years": st.column_config.NumberColumn(format="%d%%")})
        st.markdown("**And once a stock is 70%+ below its high, what preceded the recoveries** (12 months after buying at the next open):")
        tb = pd.DataFrame(dw.BACKTEST_TURN_ROWS)
        st.dataframe(tb, hide_index=True, width="stretch", column_config={
            "Median after 12 months": st.column_config.NumberColumn(format="%.1f%%"), "Lost 30%+": st.column_config.NumberColumn("Lost 30% or more", format="%d%%"),
            "Positive after 12 months": st.column_config.NumberColumn(format="%d%%")})
        st.markdown("Waiting for the turn gives up the first leg of the recovery, often 30 to 50% off the low, but cut the share that lost another 30% from about 40% to 7%. Falling-knife buying (a new low in the last month) was flat after a year.")
        st.markdown(
            "- The list of stocks only contains companies that are still listed, so the ones that fell and never came back are missing: **real results will be weaker**.\n"
            "- Signals bunch together in market-wide falls (2018, 2020, 2022, 2025), so many of your stocks can reach a buy zone in the same week.\n"
            "- In a portfolio test, buying only 45%+ falls and selling on recovery made about 11 to 12% a year after tax, because money waits for signals and recoveries take 1.5 to 2 years. "
            "The return on money actually invested was much higher; since you add money every month, waiting costs you less than it costs the test.\n"
            "- Stocks that did not regain the old high within 3 years lost 20% to 90%, mostly small companies. Keep each position small enough to live with that."
        )


watch_tab, scan_tab = st.tabs(["⭐ My watchlist", "🔎 Scan all stocks"])
with watch_tab:
    _watchlist_tab()
with scan_tab:
    _scan_tab()
_backtest()
