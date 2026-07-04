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


def analyze(
    raw: pd.DataFrame,
    exit_rules: ExitRules,
    sizing_rules: SizingRules,
    capital_base: float | None = None,
    live_prices: dict[str, float] | None = None,
    as_of=None,
) -> PositionGuideResult:
    if raw.empty:
        return PositionGuideResult(ok=False, message="No open positions found in the workbook.")

    df = apply_live_prices(raw, live_prices) if live_prices else raw.copy()
    if "CMP Source" not in df:
        df["CMP Source"] = "sheet"

    lots = compute_lot_metrics(df, exit_rules, as_of=as_of)
    base = capital_base if capital_base else lots["Buy Value"].sum()
    stocks = compute_stock_summary(lots, sizing_rules, base)
    exits = build_exit_table(lots)
    add_candidates = compute_add_candidates(lots, sizing_rules, base)

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
