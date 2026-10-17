"""
Position sizing & exit-signal guide for Sai's live open positions.

Reads the "Open Positions" tab of his personal trades workbook (one row per
buy lot - he adds to names in tranches and can exit lots individually) and
applies two rules he manages by hand:

Sizing (informational only - he executes this himself, this just surfaces
where each name stands against it):
  - max ~5% of deployed capital per stock (allocation % = that stock's total
    Buy Value across all its open lots / total deployed Buy Value)
  - built in up to 3 tranches
  - next add trigger = 17% below the last buy price

Exit (the actual decision this page is meant to help with) - a sliding bar,
evaluated per buy lot since he exits tranches individually rather than
always closing a whole position at once:
  - short holding period: exit on a flat absolute gain (quick win)
  - once it's been held longer, judge it on annualized return (CAGR) instead
  - the CAGR bar he'll accept keeps dropping the longer it's been held

CMP is refreshed from Yahoo Finance (NSE, ".NS" tickers) instead of the
static value stored in the workbook, since that's usually stale.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import quote

import numpy as np
import pandas as pd
import yfinance as yf

from strategies import price_store

CHUNK_SIZE = 50
GOOGLE_SHEET_ID_RE = re.compile(r"docs\.google\.com/spreadsheets/d/([a-zA-Z0-9-_]+)")

# Column order in the "Open Positions" tab (same in the workbook and the Google
# Sheet copy). The Google Sheet's gviz CSV export drops most header labels (the
# header row has real text only in a few cells), so those columns come back as
# blank names - positional mapping is the only reliable way to read it.
_OPEN_POSITIONS_COLUMNS = [
    "aa", "Stock", "Buy Date", "Buy Price", "Qty", "Buy Value Sheet", "Strategy Name",
    "CMP Sheet", "Current Value Sheet", "% Gain Sheet", "Gain Sheet", "Years Held Sheet",
    "% Annual Gain Sheet", "Rule", "Target Price", "Total Potential Gain", "Remaining Gain",
    "Target Value", "Sector", "Industry", "Platform", "Month of Buy", "Classification",
]


@dataclass
class ExitRules:
    short_term_years: float = 1.0
    short_term_gain: float = 0.25
    mid_term_years: float = 2.0
    mid_term_cagr: float = 0.20
    long_term_cagr: float = 0.15


@dataclass
class SizingRules:
    max_allocation_pct: float = 0.05
    max_tranches: int = 7
    add_on_drop_pct: float = 0.17
    min_price_gap_pct: float = 0.15


@dataclass
class PositionGuideResult:
    ok: bool
    message: str = ""
    lots: pd.DataFrame = field(default_factory=pd.DataFrame)
    stocks: pd.DataFrame = field(default_factory=pd.DataFrame)
    exits: pd.DataFrame = field(default_factory=pd.DataFrame)
    add_candidates: pd.DataFrame = field(default_factory=pd.DataFrame)
    capital_base: float = 0.0
    live_price_count: int = 0
    fallback_symbols: list = field(default_factory=list)


def is_google_sheet_url(source) -> bool:
    return isinstance(source, str) and "docs.google.com/spreadsheets" in source


def _clean_numeric(series: pd.Series) -> pd.Series:
    return pd.to_numeric(
        series.astype(str).str.replace(",", "", regex=False).str.replace("%", "", regex=False).str.strip(),
        errors="coerce",
    )


def _load_from_gsheet(url: str, sheet_name: str = "Open Positions") -> pd.DataFrame:
    """Read a tab of a public ("anyone with the link can view") Google Sheet
    via its CSV export - no credentials needed, but note the CSV reflects the
    sheet's *displayed* number formatting, so values are rounded to whatever
    precision the sheet shows (e.g. Buy Price to 2 decimals) rather than the
    full underlying precision an .xlsx export would carry.
    """
    match = GOOGLE_SHEET_ID_RE.search(url)
    if not match:
        raise ValueError(f"Could not find a Google Sheet ID in: {url}")
    csv_url = f"https://docs.google.com/spreadsheets/d/{match.group(1)}/gviz/tq?tqx=out:csv&sheet={quote(sheet_name)}"
    raw = pd.read_csv(csv_url)
    raw = raw.iloc[:, : len(_OPEN_POSITIONS_COLUMNS)].copy()
    raw.columns = _OPEN_POSITIONS_COLUMNS

    raw = raw.dropna(subset=["Stock", "Buy Date"]).copy()
    raw["Buy Date"] = pd.to_datetime(raw["Buy Date"], format="%d-%b-%y", errors="coerce")
    raw = raw.dropna(subset=["Buy Date"])
    raw["Buy Price"] = _clean_numeric(raw["Buy Price"])
    raw["Qty"] = _clean_numeric(raw["Qty"])
    raw["Buy Value"] = raw["Buy Price"] * raw["Qty"]
    raw["CMP"] = _clean_numeric(raw["CMP Sheet"])
    return raw


def load_open_positions(source) -> pd.DataFrame:
    if is_google_sheet_url(source):
        df = _load_from_gsheet(source)
    else:
        df = pd.read_excel(source, sheet_name="Open Positions")
        df = df.dropna(subset=["Stock", "Buy Date"]).copy()
        df["Buy Date"] = pd.to_datetime(df["Buy Date"])

    keep = ["Stock", "Buy Date", "Buy Price", "Qty", "Buy Value", "CMP",
            "Strategy Name", "Rule", "Sector", "Industry", "Platform"]
    return df[[c for c in keep if c in df.columns]].reset_index(drop=True)


def fetch_live_prices(symbols: list[str], chunk_size: int = CHUNK_SIZE) -> dict[str, float]:
    """Best-effort latest traded price per symbol via Yahoo Finance (NSE ".NS" tickers).

    Uses 1-minute bars over the current session so it reflects the latest
    print during market hours (falls back to the prior session's last bar
    when the market is closed). Symbols it can't resolve are simply omitted -
    callers should fall back to the workbook's stored CMP for those.
    """
    uniq = sorted({s for s in symbols if isinstance(s, str) and s})
    prices: dict[str, float] = {}
    for i in range(0, len(uniq), chunk_size):
        chunk = uniq[i:i + chunk_size]
        yahoo_symbols = [f"{s}.NS" for s in chunk]
        try:
            data = yf.download(
                yahoo_symbols, period="1d", interval="1m",
                group_by="ticker", threads=True, progress=False, auto_adjust=False,
            )
        except Exception:
            continue
        if data is None or data.empty:
            continue
        for sym, ysym in zip(chunk, yahoo_symbols):
            try:
                sub = data["Close"] if len(chunk) == 1 else data[ysym]["Close"]
                sub = sub.dropna()
            except (KeyError, TypeError):
                continue
            if not sub.empty:
                prices[sym] = float(sub.iloc[-1])
    return prices


NIFTY_TICKER = "^NSEI"
RS_WINDOWS = {"1M": 30, "3M": 91}  # calendar days
RS_COLUMNS = [f"RS {w}" for w in RS_WINDOWS]


def _window_return(close: pd.Series, days: int) -> float:
    """Return over the last `days` calendar days, NaN if history doesn't reach back that far."""
    close = close.dropna()
    if close.empty:
        return np.nan
    cutoff = close.index[-1] - pd.Timedelta(days=days)
    if close.index[0] > cutoff:
        return np.nan
    base = close.loc[:cutoff]
    if base.empty or base.iloc[-1] <= 0:
        return np.nan
    return float(close.iloc[-1] / base.iloc[-1] - 1.0)


TECH_COLUMNS = ["52W High Drop", "ATH Drop", "vs 200DMA", "Vol Trend", "Up/Down Vol", "VP Low (VAL)", "VP POC", "VP High (VAH)", "Overhead Supply", "Supply to +25%",
                "RSI 14", "Days Since Low", "Volatility Ratio", "Off 52W Low"]
# Avg Value 20D = avg daily traded value, Rs crore; Worst 1D Move = biggest one-day close-to-close fall in the
# last year (a -35% or worse day is normally a demerger / unadjusted corporate action, not a real crash)
MARKET_EXTRA_COLUMNS = ["Last Close", "Avg Value 20D", "Worst 1D Move"]
# Inputs for the Trend Template page: simple moving averages (with the 200-day as it stood 1 and 4 months ago) and
# returns over ~3/6/9/12 months of trading sessions (63/126/189/252), which feed its relative-strength rating.
TREND_COLUMNS = ["SMA 50", "SMA 150", "SMA 200", "SMA 200 1M Ago", "SMA 200 4M Ago", "Perf 3M", "Perf 6M", "Perf 9M", "Perf 12M"]
PERF_SESSIONS = {"Perf 3M": 63, "Perf 6M": 126, "Perf 9M": 189, "Perf 12M": 252}
VP_BINS = 40
VP_VALUE_AREA = 0.70
VP_LOOKBACK = 126  # ~6 months of sessions


def volume_profile(df: pd.DataFrame, ref_price: float | None = None, band_pct: float = 0.25, bins: int = VP_BINS, value_area: float = VP_VALUE_AREA):
    """Fixed-range volume profile over `df` (needs High/Low/Volume columns).

    Each day's volume is spread evenly over the price bins its low-high range
    touches. Returns (VAL, POC, VAH, overhead, band_supply): POC is the price bin with the
    most volume, VAL/VAH bound the contiguous zone around it holding
    `value_area` of total volume, and overhead is the share of the window's
    volume that traded above `ref_price` (supply from holders who bought
    higher and may sell into a recovery), and band_supply is the share that
    traded between `ref_price` and `ref_price * (1 + band_pct)` - the supply
    standing in the way of a `band_pct` move up. NaNs if the range is degenerate.
    """
    d = df.dropna(subset=["High", "Low", "Volume"])
    d = d[(d["Volume"] > 0) & (d["High"] >= d["Low"])]
    if len(d) < 5:
        return (np.nan,) * 5
    lo, hi = float(d["Low"].min()), float(d["High"].max())
    if hi <= lo:
        return (np.nan,) * 5
    edges = np.linspace(lo, hi, bins + 1)
    vol = np.zeros(bins)
    for h, l, v in zip(d["High"].to_numpy(), d["Low"].to_numpy(), d["Volume"].to_numpy()):
        i0 = min(bins - 1, int(np.searchsorted(edges, l, side="right") - 1))
        i1 = min(bins - 1, int(np.searchsorted(edges, h, side="right") - 1))
        i0 = max(i0, 0)
        vol[i0:i1 + 1] += v / (i1 - i0 + 1)
    poc = int(vol.argmax())
    lo_i = hi_i = poc
    covered, target = vol[poc], vol.sum() * value_area
    while covered < target and (lo_i > 0 or hi_i < bins - 1):
        down = vol[lo_i - 1] if lo_i > 0 else -1.0
        up = vol[hi_i + 1] if hi_i < bins - 1 else -1.0
        if up >= down:
            hi_i += 1
            covered += vol[hi_i]
        else:
            lo_i -= 1
            covered += vol[lo_i]
    centers = (edges[:-1] + edges[1:]) / 2
    overhead = float(vol[centers > ref_price].sum() / vol.sum()) if ref_price is not None else np.nan
    band = (
        float(vol[(centers > ref_price) & (centers <= ref_price * (1 + band_pct))].sum() / vol.sum())
        if ref_price is not None else np.nan
    )
    return float(edges[lo_i]), float(centers[poc]), float(edges[hi_i + 1]), overhead, band


def _technicals(hist: pd.DataFrame) -> dict:
    """Technical attributes from ~14 months of daily OHLCV for one stock."""
    out = {c: np.nan for c in TECH_COLUMNS + MARKET_EXTRA_COLUMNS + TREND_COLUMNS}
    hist = hist.dropna(subset=["Close"])
    if len(hist) < 30:
        return out
    close, vol = hist["Close"], hist["Volume"]
    last = float(close.iloc[-1])
    out["Last Close"] = last
    out["Avg Value 20D"] = float((close.iloc[-20:] * vol.iloc[-20:]).mean() / 1e7)

    year = hist.loc[hist.index >= hist.index[-1] - pd.Timedelta(days=365)]
    high_52w = float(year["Close"].max())
    out["52W High Drop"] = last / high_52w - 1.0
    out["Worst 1D Move"] = float(year["Close"].pct_change().min())
    if len(close) >= 200:
        out["vs 200DMA"] = last / float(close.iloc[-200:].mean()) - 1.0
        out["SMA 200"] = float(close.iloc[-200:].mean())
        if len(close) >= 221:
            out["SMA 200 1M Ago"] = float(close.iloc[-221:-21].mean())
        if len(close) >= 284:
            out["SMA 200 4M Ago"] = float(close.iloc[-284:-84].mean())
    if len(close) >= 50:
        out["SMA 50"] = float(close.iloc[-50:].mean())
    if len(close) >= 150:
        out["SMA 150"] = float(close.iloc[-150:].mean())
    for name, n in PERF_SESSIONS.items():
        if len(close) > n and close.iloc[-1 - n] > 0:
            out[name] = last / float(close.iloc[-1 - n]) - 1.0
    if len(vol) >= 90 and vol.iloc[-90:].mean() > 0:
        out["Vol Trend"] = float(vol.iloc[-20:].mean() / vol.iloc[-90:].mean())

    # Stabilisation: how long since the 52W closing low, and how far off it we are.
    low_pos = int(np.argmin(year["Close"].to_numpy()))
    out["Days Since Low"] = float(len(year) - 1 - low_pos)
    out["Off 52W Low"] = last / float(year["Close"].min()) - 1.0

    # RSI(14), Wilder smoothing.
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    if loss.iloc[-1] > 0:
        out["RSI 14"] = float(100 - 100 / (1 + gain.iloc[-1] / loss.iloc[-1]))
    elif gain.iloc[-1] > 0:
        out["RSI 14"] = 100.0

    # Volatility contraction: avg true range (as % of price) last 20 sessions vs last 100.
    if len(hist) >= 101:
        prev = close.shift(1)
        tr = pd.concat([hist["High"] - hist["Low"], (hist["High"] - prev).abs(), (hist["Low"] - prev).abs()], axis=1).max(axis=1)
        tr_pct = (tr / close).dropna()
        if tr_pct.iloc[-100:].mean() > 0:
            out["Volatility Ratio"] = float(tr_pct.iloc[-20:].mean() / tr_pct.iloc[-100:].mean())

    recent = hist.iloc[-60:]
    chg = recent["Close"].diff()
    up_v, dn_v = recent.loc[chg > 0, "Volume"].sum(), recent.loc[chg < 0, "Volume"].sum()
    if dn_v > 0:
        out["Up/Down Vol"] = float(up_v / dn_v)

    # Trailing ~6-month fixed-range profile: recent enough to reflect where the
    # current base was built (an anchor at the 52W high breaks on stocks that
    # crashed - the POC ends up far above any price they will see again).
    out["VP Low (VAL)"], out["VP POC"], out["VP High (VAH)"], out["Overhead Supply"], out["Supply to +25%"] = volume_profile(
        hist.iloc[-VP_LOOKBACK:], ref_price=last
    )
    return out


def fetch_market_stats(symbols: list[str], force: bool = False, update: bool = True) -> pd.DataFrame:
    """Per-symbol market stats computed from the local daily-price store
    (strategies/price_store.py), which is topped up with only the sessions that
    are new since the last run.

    - RS 1M/3M: relative strength vs Nifty 50 = stock return minus Nifty
      return over the window (return fractions; positive = beating Nifty).
    - 52W High Drop, ATH Drop (vs the all-time closing high kept by price_store.update_ath), vs 200DMA, Vol Trend (20d vs 90d avg volume), Up/Down Vol
      (volume on up days / down days, last 60 sessions), RSI, stabilisation and
      volatility fields - see _technicals.
    - Volume profile (VAL / POC / VAH, overhead supply) over the last ~6 months.

    Uses last completed daily bars. Unresolved symbols or insufficient history
    give NaN for the affected fields. `force` re-checks Yahoo even if the store
    looks current; `update=False` skips the Yahoo top-up and uses the store as it is.
    """
    uniq = sorted({s for s in symbols if isinstance(s, str) and s})
    out = pd.DataFrame(index=pd.Index(uniq, name="Stock"), columns=RS_COLUMNS + TECH_COLUMNS + MARKET_EXTRA_COLUMNS + TREND_COLUMNS, dtype=float)
    if not uniq:
        return out

    if update:
        price_store.update_prices(uniq + [NIFTY_TICKER], force=force)
    history = price_store.load_history(uniq + [NIFTY_TICKER])
    try:
        ath = price_store.load_ath(uniq)  # all-time closing highs, refreshed weekly by the daily job
    except Exception:
        ath = {}

    nifty_ret = {w: np.nan for w in RS_WINDOWS}
    if NIFTY_TICKER in history:
        nifty_close = history[NIFTY_TICKER]["Close"].dropna()
        nifty_ret = {w: _window_return(nifty_close, d) for w, d in RS_WINDOWS.items()}

    for sym in uniq:
        hist = history.get(sym)
        if hist is None or hist.empty:
            continue
        for w, d in RS_WINDOWS.items():
            out.loc[sym, f"RS {w}"] = _window_return(hist["Close"], d) - nifty_ret[w]
        for k, v in _technicals(hist).items():
            out.loc[sym, k] = v
        if sym in ath:  # the stored high may be a few days old: a newer high in the price store wins
            top = max(ath[sym][0], float(hist["Close"].max()))
            out.loc[sym, "ATH Drop"] = float(hist["Close"].dropna().iloc[-1]) / top - 1.0
    return out


def apply_live_prices(df: pd.DataFrame, live_prices: dict[str, float]) -> pd.DataFrame:
    out = df.copy()
    out["CMP Source"] = "sheet"
    for sym, price in live_prices.items():
        mask = out["Stock"] == sym
        out.loc[mask, "CMP"] = price
        out.loc[mask, "CMP Source"] = "live"
    return out


def _exit_signal(row: pd.Series, rules: ExitRules) -> tuple[str, str]:
    years, gain, cagr = row["Holding Years"], row["Gain %"], row["CAGR"]
    if years < rules.short_term_years:
        held = f"{years * 12:.0f}m"
        if gain >= rules.short_term_gain:
            return "Exit", f"{gain:+.0%} in {held} (>= {rules.short_term_gain:.0%} short-term target)"
        return "Hold", f"{gain:+.0%} in {held}, below {rules.short_term_gain:.0%} short-term target"
    bar = rules.mid_term_cagr if years < rules.mid_term_years else rules.long_term_cagr
    bar_label = "mid-term" if years < rules.mid_term_years else "long-term"
    if cagr >= bar:
        return "Exit", f"CAGR {cagr:+.0%} over {years:.1f}y >= {bar:.0%} {bar_label} bar"
    return "Hold", f"CAGR {cagr:+.0%} over {years:.1f}y, below {bar:.0%} {bar_label} bar"


def compute_lot_metrics(df: pd.DataFrame, rules: ExitRules, as_of=None) -> pd.DataFrame:
    as_of = pd.Timestamp(as_of) if as_of is not None else pd.Timestamp.today().normalize()
    out = df.copy()
    out["Holding Years"] = (as_of - out["Buy Date"]).dt.days / 365.0
    out["Current Value"] = out["CMP"] * out["Qty"]
    out["Gain %"] = out["CMP"] / out["Buy Price"] - 1.0
    years_safe = out["Holding Years"].clip(lower=1 / 365)
    out["CAGR"] = (1.0 + out["Gain %"]).clip(lower=1e-6) ** (1.0 / years_safe) - 1.0

    signals = out.apply(lambda r: _exit_signal(r, rules), axis=1, result_type="expand")
    out["Signal"], out["Reason"] = signals[0], signals[1]

    out = out.sort_values(["Stock", "Buy Date"]).reset_index(drop=True)
    out["Tranche #"] = out.groupby("Stock").cumcount() + 1
    out["Tranche Count"] = out.groupby("Stock")["Tranche #"].transform("max")
    return out


def build_exit_table(lots: pd.DataFrame) -> pd.DataFrame:
    exits = lots[lots["Signal"] == "Exit"].copy()
    if exits.empty:
        return exits
    exits["Tranche"] = exits["Tranche #"].astype(str) + " of " + exits["Tranche Count"].astype(str)
    cols = ["Stock", "Tranche", "Buy Date", "Buy Price", "Qty", "CMP",
            "Gain %", "Current Value", "CAGR", "Holding Years", "Reason"]
    return exits[[c for c in cols if c in exits.columns]].sort_values(
        ["Stock", "Buy Date"]
    ).reset_index(drop=True)


def _pair_gap_pct(price_a: float, price_b: float) -> float:
    """% gap between two prices, relative to the higher one (so it reads as
    "how far below the higher price is the lower one", matching how the
    17%/15% drop thresholds are already expressed)."""
    return abs(price_a - price_b) / max(price_a, price_b)


def compute_add_candidates(lots: pd.DataFrame, sizing: SizingRules, capital_base: float) -> pd.DataFrame:
    """Add-more candidates: stocks with tranches still available.

    "Down %" is evaluated against every existing buy price for the stock,
    not just the last one - it's the worst case: how far CMP sits below the
    *closest* buy price (negative if CMP is still above any one of them).
    That single number drives the status:
      - >= add_on_drop_pct           -> Ready   (cleared every buy by enough)
      - [min_price_gap_pct, add_on_drop_pct) -> Nearing (getting close)
      - < min_price_gap_pct          -> Blocked (still clustering on a past buy,
                                                  or hasn't dropped at all)

    Max Buy Allowed is simply the room left under the 5% cap (5% of capital -
    what's already deployed in that stock).

    "Past Price-Gap Flag" is a separate, informational audit: flags stocks
    where two of the buys *already taken* sit within min_price_gap_pct of
    each other. It doesn't affect the status above.
    """
    rows = []
    for stock, g in lots.groupby("Stock", sort=False):
        g = g.sort_values("Buy Date")
        tranches = len(g)
        remaining_tranches = max(0, sizing.max_tranches - tranches)
        if remaining_tranches <= 0:
            continue

        buy_value = g["Buy Value"].sum()
        alloc_pct = buy_value / capital_base if capital_base else np.nan

        last_buy_price = g.iloc[-1]["Buy Price"]
        cmp = g["CMP"].iloc[-1]
        buy_prices = g["Buy Price"].tolist()
        buy_dates = g["Buy Date"].dt.strftime("%Y-%m-%d").tolist()

        # Retrospective audit: were any two of the buys already taken too close together?
        past_flag = ""
        for i in range(len(buy_prices)):
            for j in range(i + 1, len(buy_prices)):
                gap = _pair_gap_pct(buy_prices[i], buy_prices[j])
                if gap < sizing.min_price_gap_pct:
                    past_flag = f"{buy_dates[i]} (₹{buy_prices[i]:.0f}) & {buy_dates[j]} (₹{buy_prices[j]:.0f}) only {gap:.0%} apart"
                    break
            if past_flag:
                break

        # Down % vs. every existing buy price - the worst case (closest one) wins.
        # Positive = CMP below that buy by this much; negative = CMP is still above it.
        drops = [(p - cmp) / p for p in buy_prices]
        down_pct = min(drops) if drops else np.nan

        if down_pct >= sizing.add_on_drop_pct:
            add_status = "Ready"
        elif down_pct >= sizing.min_price_gap_pct:
            add_status = "Nearing"
        else:
            add_status = "Blocked"

        max_buy_allowed = max(0.0, capital_base * sizing.max_allocation_pct - buy_value) if capital_base else np.nan

        rows.append({
            "Stock": stock,
            "Tranches Bought": tranches,
            "Allocation %": alloc_pct,
            "Last Buy Price": last_buy_price,
            "CMP": cmp,
            "Down %": down_pct,
            "Max Buy Allowed": max_buy_allowed,
            "Add Status": add_status,
            "Past Price-Gap Flag": past_flag,
        })

    result = pd.DataFrame(rows)
    if result.empty:
        return result
    status_order = {"Ready": 0, "Nearing": 1, "Blocked": 2}
    result["_order"] = result["Add Status"].map(status_order)
    return result.sort_values(["_order", "Down %"], ascending=[True, False]).drop(columns="_order").reset_index(drop=True)


def compute_stock_summary(lots: pd.DataFrame, sizing: SizingRules, capital_base: float) -> pd.DataFrame:
    rows = []
    for stock, g in lots.groupby("Stock", sort=False):
        g = g.sort_values("Buy Date")
        qty = g["Qty"].sum()
        buy_value = g["Buy Value"].sum()
        current_value = g["Current Value"].sum()
        avg_price = buy_value / qty if qty else np.nan
        cmp = g["CMP"].iloc[-1]
        last_buy_price = g.iloc[-1]["Buy Price"]
        gain_pct = current_value / buy_value - 1.0 if buy_value else np.nan
        tranches = len(g)
        alloc_pct = buy_value / capital_base if capital_base else np.nan
        remaining_tranches = max(0, sizing.max_tranches - tranches)
        under_cap = alloc_pct < sizing.max_allocation_pct if not np.isnan(alloc_pct) else True
        add_trigger_price = last_buy_price * (1 - sizing.add_on_drop_pct)
        add_ready = remaining_tranches > 0 and under_cap and cmp <= add_trigger_price

        exit_lots = g.loc[g["Signal"] == "Exit"]
        if len(exit_lots) == tranches and tranches > 0:
            action = "Exit"
        elif len(exit_lots) > 0:
            action = "Exit (partial)"
        elif add_ready:
            action = "Add More"
        elif not under_cap:
            action = "Hold (at cap)"
        else:
            action = "Hold"

        rows.append({
            "Stock": stock,
            "Sector": g["Sector"].iloc[-1] if "Sector" in g else None,
            "Tranches": tranches,
            "Qty": qty,
            "Avg Buy Price": avg_price,
            "Last Buy Price": last_buy_price,
            "CMP": cmp,
            "Buy Value": buy_value,
            "Current Value": current_value,
            "Gain %": gain_pct,
            "Allocation %": alloc_pct,
            "Remaining Tranches": remaining_tranches,
            "Add Trigger Price": add_trigger_price if remaining_tranches > 0 else np.nan,
            "Action": action,
        })

    order = {"Exit": 0, "Exit (partial)": 1, "Add More": 2, "Hold": 3, "Hold (at cap)": 4}
    result = pd.DataFrame(rows)
    result["_order"] = result["Action"].map(order)
    return result.sort_values(["_order", "Gain %"], ascending=[True, False]).drop(columns="_order").reset_index(drop=True)


def _scale(x, lo, hi) -> float:
    """Linear 0..1 between lo (0) and hi (1), clipped; NaN -> 0 (no evidence = no credit)."""
    if x is None or pd.isna(x):
        return 0.0
    return float(np.clip((x - lo) / (hi - lo), 0.0, 1.0))


BUY_SCORE_WEIGHTS = {
    "Stabilisation": 25,   # no fresh low for 30+ sessions (a falling stock isn't a base yet)
    "Accumulation": 25,    # volume on up-days beating down-days
    "Calm": 15,            # volatility contracting (tightening base)
    "Strength": 15,        # beating Nifty over 3M
    "Clear path": 20,      # little supply between CMP and +25%
}


def buy_score(row: pd.Series) -> float:
    """0-100 timing score for *when* to add to a stock you already decided is cheap
    enough. Built to mirror the base-building framework (decline -> stabilise ->
    accumulate -> breakout), since there is no stop-loss to bail out a falling knife."""
    parts = {
        "Stabilisation": _scale(row.get("Days Since Low"), 5, 30),
        "Accumulation": _scale(row.get("Up/Down Vol"), 0.8, 1.5),
        "Calm": _scale(row.get("Volatility Ratio"), 1.2, 0.6),
        "Strength": _scale(row.get("RS 3M"), -0.10, 0.10),
        "Clear path": _scale(row.get("Supply to +25%"), 0.80, 0.40),
    }
    return float(sum(BUY_SCORE_WEIGHTS[k] * v for k, v in parts.items()))


def _vp_zone(row: pd.Series) -> str:
    cmp, val, poc, vah = row["CMP"], row["VP Low (VAL)"], row["VP POC"], row["VP High (VAH)"]
    if any(pd.isna(x) for x in (cmp, val, poc, vah)):
        return ""
    if cmp < val:
        return "Below value area"
    if cmp > vah:
        return "Above value area"
    return "At POC" if abs(cmp / poc - 1.0) <= 0.03 else "In value area"


def analyze(
    raw: pd.DataFrame,
    exit_rules: ExitRules,
    sizing_rules: SizingRules,
    capital_base: float | None = None,
    live_prices: dict[str, float] | None = None,
    as_of=None,
    market_stats: pd.DataFrame | None = None,
) -> PositionGuideResult:
    if raw.empty:
        return PositionGuideResult(ok=False, message="No open positions found in the workbook.")

    df = apply_live_prices(raw, live_prices) if live_prices else raw.copy()
    if "CMP Source" not in df:
        df["CMP Source"] = "sheet"

    lots = compute_lot_metrics(df, exit_rules, as_of=as_of)
    base = capital_base if capital_base else lots["Buy Value"].sum()
    stocks = compute_stock_summary(lots, sizing_rules, base)
    if market_stats is not None and not market_stats.empty:
        ms = market_stats.reindex(stocks["Stock"])
        for col in RS_COLUMNS + TECH_COLUMNS:
            if col in ms:
                stocks[col] = ms[col].to_numpy()
        if "VP POC" in stocks:
            stocks["vs POC"] = stocks["CMP"] / stocks["VP POC"] - 1.0
            stocks["VP Zone"] = stocks.apply(_vp_zone, axis=1)
        stocks["Buy Score"] = stocks.apply(buy_score, axis=1)
    exits = build_exit_table(lots)
    add_candidates = compute_add_candidates(lots, sizing_rules, base)
    if "RS 3M" in stocks:
        by_stock = stocks.set_index("Stock")
        if not exits.empty:
            exits["RS 3M"] = exits["Stock"].map(by_stock["RS 3M"])
        if not add_candidates.empty:
            for col in ["Buy Score", "Up/Down Vol", "Days Since Low", "Volatility Ratio", "RSI 14",
                        "Supply to +25%", "VP Zone", "RS 3M"]:
                if col in by_stock:
                    add_candidates[col] = add_candidates["Stock"].map(by_stock[col])
            # Rank within each status band by timing score, then flag the top 3 actionable names.
            order = {"Ready": 0, "Nearing": 1, "Blocked": 2}
            add_candidates["_o"] = add_candidates["Add Status"].map(order)
            add_candidates = add_candidates.sort_values(["_o", "Buy Score"], ascending=[True, False]).drop(columns="_o").reset_index(drop=True)
            actionable = add_candidates["Add Status"] != "Blocked"
            add_candidates["Pick"] = ""
            add_candidates.loc[add_candidates.index[actionable][:3], "Pick"] = "⭐ Top 3"

    live_count = int((lots["CMP Source"] == "live").sum())
    fallback = sorted(lots.loc[lots["CMP Source"] == "sheet", "Stock"].unique().tolist())

    return PositionGuideResult(
        ok=True,
        message=f"{len(stocks)} open stocks across {len(lots)} lots, as of {pd.Timestamp(as_of or pd.Timestamp.today().normalize()).date()}.",
        lots=lots,
        stocks=stocks,
        exits=exits,
        add_candidates=add_candidates,
        capital_base=base,
        live_price_count=live_count,
        fallback_symbols=fallback,
    )
