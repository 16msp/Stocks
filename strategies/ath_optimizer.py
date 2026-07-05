"""
Per-ETF entry/exit parameter optimizer for the ATH-drop averaging strategy.

The main ath_averaging strategy uses one fixed rule (-20% entry, +25%/15% CAGR
exit) for every ETF. This module instead sweeps entry-drop-from-ATH and exit
targets independently *per ETF* against its own historical data, and reports
whichever combination performed best - since a sensible drawdown-entry depth
and exit bar genuinely differs by instrument (a defensive gilt ETF and a
volatile smallcap ETF shouldn't use the same numbers).

This is a research/planning table, not tied to the live paper-trading
tracker in ath_averaging.py: it answers "if I tuned per ETF, what would have
worked, and how close is the market right now to actually triggering that,"
rather than opening real (paper) positions itself.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from strategies import ath_averaging as ath
from strategies import nse_etf_momentum as base

TRADE_SIZE = ath.TRADE_SIZE
AVG_DROP_TRIGGER = ath.AVG_DROP_TRIGGER
MAX_TRANCHES = ath.MAX_TRANCHES
SHORT_TERM_DAYS = ath.SHORT_TERM_DAYS

ENTRY_DROP_GRID = (0.15, 0.18, 0.20, 0.25, 0.30, 0.35)
SHORT_TERM_GAIN_GRID = (0.15, 0.20, 0.25, 0.30)
LONG_TERM_CAGR_GRID = (0.12, 0.15, 0.18, 0.20)

MIN_YEARS_FOR_OPTIMIZATION = 1.0
MIN_TRADES_FOR_XIRR_OBJECTIVE = 3  # below this, an XIRR "winner" is a small-sample fluke


def _compute_xirr(trades: pd.DataFrame, as_of) -> float | None:
    if trades.empty:
        return None
    cash_flows = []
    for _, row in trades.iterrows():
        cash_flows.append((-TRADE_SIZE, pd.Timestamp(row["entry_date"]).date()))
        exit_date = pd.Timestamp(row["exit_date"]).date() if row["status"] == "WIN" else pd.Timestamp(as_of).date()
        cash_flows.append((TRADE_SIZE * (1 + row["return_pct"] / 100), exit_date))
    t0 = min(d for _, d in cash_flows)

    def npv(rate):
        return sum(amt / (1 + rate) ** ((d - t0).days / 365.0) for amt, d in cash_flows)

    lo, hi = -0.9999, 20.0
    f_lo, f_hi = npv(lo), npv(hi)
    if f_lo * f_hi > 0:
        return None
    for _ in range(200):
        mid = (lo + hi) / 2
        f_mid = npv(mid)
        if abs(f_mid) < 1e-6:
            break
        if f_lo * f_mid < 0:
            hi = mid
        else:
            lo, f_lo = mid, f_mid
    return mid * 100


def _simulate(df: pd.DataFrame, entry_drop: float, short_term_gain: float, long_term_cagr: float):
    """One (entry, exit) combo against one symbol's history. Returns (summary dict, trades_df) or None."""
    dates = df["date"].to_numpy()
    close = df["close"].to_numpy()
    open_ = df["open"].to_numpy()
    open_valid = ~np.isnan(open_)
    day_high = np.where(open_valid, np.maximum(open_, close), close)
    day_low = np.where(open_valid, np.minimum(open_, close), close)
    ath_series = np.maximum.accumulate(np.nan_to_num(close, nan=-np.inf))
    n = len(df)
    if n == 0:
        return None
    as_of = dates[-1]

    trades: list[dict] = []
    cycle: list[dict] = []
    next_tranche = 1

    for t in range(n):
        today_ath = ath_series[t]
        if next_tranche == 1:
            trigger = today_ath * (1 - entry_drop)
            if day_low[t] <= trigger:
                cycle.append({"tranche": 1, "entry_date": dates[t], "entry_price": trigger, "status": "OPEN"})
                next_tranche = 2
        elif next_tranche in (2, 3):
            last_price = cycle[-1]["entry_price"]
            trigger = last_price * (1 - AVG_DROP_TRIGGER)
            if day_low[t] <= trigger:
                cycle.append({"tranche": next_tranche, "entry_date": dates[t], "entry_price": trigger, "status": "OPEN"})
                next_tranche = next_tranche + 1 if next_tranche < MAX_TRANCHES else None

        for tr in cycle:
            if tr["status"] != "OPEN":
                continue
            days_held = int((dates[t] - tr["entry_date"]) / np.timedelta64(1, "D"))
            target_return = short_term_gain if days_held < SHORT_TERM_DAYS else (1 + long_term_cagr) ** (days_held / 365.0) - 1
            target_price = tr["entry_price"] * (1 + target_return)
            if day_high[t] >= target_price:
                tr.update(status="WIN", exit_date=dates[t], days_held=days_held, return_pct=target_return * 100)

        if cycle and all(tr["status"] != "OPEN" for tr in cycle):
            trades.extend(cycle)
            cycle = []
            next_tranche = 1

    for tr in cycle:
        if tr["status"] == "OPEN":
            days_held = int((as_of - tr["entry_date"]) / np.timedelta64(1, "D"))
            tr.update(status="PENDING", days_held=days_held, return_pct=(close[-1] / tr["entry_price"] - 1) * 100)
        trades.append(tr)

    if not trades:
        return None

    out = pd.DataFrame(trades)
    out["gain_rupees"] = TRADE_SIZE * out["return_pct"] / 100
    wins = out[out["status"] == "WIN"]

    summary = {
        "entry_drop": entry_drop,
        "short_term_gain": short_term_gain,
        "long_term_cagr": long_term_cagr,
        "trades": len(out),
        "wins": len(wins),
        "pending": len(out) - len(wins),
        "win_rate_pct": len(wins) / len(out) * 100,
        "total_gain": wins["gain_rupees"].sum(),
        "xirr_pct": _compute_xirr(out, as_of),
        "as_of": as_of,
    }
    return summary, out


def optimize_symbol(df: pd.DataFrame, objective: str = "total_gain"):
    """Sweep the full grid for one symbol's history, return (best_summary, best_trades) or None."""
    best = None
    for entry_drop in ENTRY_DROP_GRID:
        for short_gain in SHORT_TERM_GAIN_GRID:
            for long_cagr in LONG_TERM_CAGR_GRID:
                result = _simulate(df, entry_drop, short_gain, long_cagr)
                if result is None:
                    continue
                summary, trades = result
                score = summary[objective]
                if objective == "xirr_pct":
                    if score is None or summary["trades"] < MIN_TRADES_FOR_XIRR_OBJECTIVE:
                        continue
                if best is None or (score is not None and score > best[0][objective]):
                    best = (summary, trades)
    return best


@dataclass
class OptimizerResult:
    ok: bool
    message: str = ""
    universe: pd.DataFrame = field(default_factory=pd.DataFrame)
    trades: pd.DataFrame = field(default_factory=pd.DataFrame)


def run_optimizer(symbols: list[str] | None = None, objective: str = "total_gain",
                   min_years: float = MIN_YEARS_FOR_OPTIMIZATION) -> OptimizerResult:
    prices = base.read_daily_prices()
    if prices.empty:
        return OptimizerResult(ok=False, message="No stored data yet.")
    prices, _bad = base.drop_bad_ticks(prices)

    if symbols is None:
        symbols = ath.get_universe_symbol_list()

    meta = base.read_meta().set_index("symbol")["category"].to_dict()
    depth = base.get_history_depth(symbols).set_index("symbol")["years_of_history"].to_dict()

    rows = []
    all_trades = []
    for sym in symbols:
        if depth.get(sym, 0.0) < min_years:
            continue
        df = prices[prices["symbol"] == sym].sort_values("date").reset_index(drop=True)
        if df.empty:
            continue
        best = optimize_symbol(df, objective=objective)
        if best is None:
            continue
        summary, trades = best

        trades = trades.copy()
        trades["symbol"] = sym
        trades["description"] = meta.get(sym, "")
        all_trades.append(trades)

        close = df["close"].to_numpy()
        ath_val = float(np.nanmax(close))
        cmp_ = float(close[-1])
        entry_drop = summary["entry_drop"]

        pending = trades[trades["status"] == "PENDING"]
        if not pending.empty:
            last_pending = pending.sort_values("tranche").iloc[-1]
            if int(last_pending["tranche"]) < MAX_TRANCHES:
                next_entry_price = float(last_pending["entry_price"]) * (1 - AVG_DROP_TRIGGER)
            else:
                next_entry_price = None  # fully loaded on this cycle, nothing more to buy
        else:
            next_entry_price = ath_val * (1 - entry_drop)

        pct_wait = ((cmp_ - next_entry_price) / cmp_ * 100) if next_entry_price else None

        rows.append({
            "symbol": sym,
            "description": meta.get(sym, ""),
            "entry_drop_pct": entry_drop * 100,
            "short_term_gain_pct": summary["short_term_gain"] * 100,
            "long_term_cagr_pct": summary["long_term_cagr"] * 100,
            "cmp": cmp_,
            "ath": ath_val,
            "next_entry_price": next_entry_price,
            "pct_wait": pct_wait,
            "win_rate_pct": summary["win_rate_pct"],
            "wins": summary["wins"],
            "open_count": summary["pending"],
            "total_trades": summary["trades"],
            "total_gain": summary["total_gain"],
            "xirr_pct": summary["xirr_pct"],
        })

    if not rows:
        return OptimizerResult(ok=False, message="No symbols had enough history to optimize.")

    universe_df = pd.DataFrame(rows).sort_values("total_gain", ascending=False).reset_index(drop=True)
    trades_df = pd.concat(all_trades, ignore_index=True) if all_trades else pd.DataFrame()
    trades_df = trades_df.sort_values(["symbol", "entry_date"]).reset_index(drop=True) if not trades_df.empty else trades_df

    return OptimizerResult(
        ok=True,
        message=f"Optimized {len(universe_df)} ETFs across {len(ENTRY_DROP_GRID) * len(SHORT_TERM_GAIN_GRID) * len(LONG_TERM_CAGR_GRID)} entry/exit combinations each.",
        universe=universe_df, trades=trades_df,
    )


# --------------------------------------------------------------------------
# Cash-flow / overlap analysis
#
# Every backtested trade ties up TRADE_SIZE (Rs.50,000) from its entry date
# until it resolves. Run enough ETFs at once and their holding periods
# overlap - this answers "how many positions are open at once, how much
# capital does that tie up, and can exits fund new entries (rotation) or do
# I need to add fresh money (infusion)?"
# --------------------------------------------------------------------------

@dataclass
class CashFlowResult:
    ok: bool
    message: str = ""
    daily: pd.DataFrame = field(default_factory=pd.DataFrame)      # date, open_count, capital_deployed
    monthly: pd.DataFrame = field(default_factory=pd.DataFrame)    # month, entries/exits counts + capital, net_flow, cumulative_balance
    peak_concurrent: int = 0
    peak_capital: float = 0.0
    peak_date: object = None
    avg_concurrent: float = 0.0
    min_cumulative_balance: float = 0.0  # most negative point of the pure-rotation-from-zero simulation


def _effective_end_date(trades_df: pd.DataFrame, as_of) -> pd.Series:
    exit_dates = pd.to_datetime(trades_df["exit_date"])
    return exit_dates.where(trades_df["status"] == "WIN", pd.Timestamp(as_of))


def compute_overlap_timeline(trades_df: pd.DataFrame, as_of) -> pd.DataFrame:
    """Daily count of concurrently open tranches (across every ETF/trade), and the capital that ties up."""
    if trades_df.empty:
        return pd.DataFrame(columns=["date", "open_count", "capital_deployed"])

    df = trades_df.copy()
    df["entry_date"] = pd.to_datetime(df["entry_date"])
    df["end_date"] = _effective_end_date(df, as_of)

    date_range = pd.date_range(df["entry_date"].min(), df["end_date"].max(), freq="D")
    delta = pd.Series(0, index=date_range, dtype=int)
    entry_counts = df["entry_date"].value_counts()
    delta.loc[entry_counts.index] += entry_counts.values
    exit_next_day = df["end_date"] + pd.Timedelta(days=1)
    exit_next_day = exit_next_day[exit_next_day <= date_range[-1]]
    exit_counts = exit_next_day.value_counts()
    delta.loc[exit_counts.index] -= exit_counts.values

    open_count = delta.cumsum()
    out = pd.DataFrame({"date": date_range, "open_count": open_count.values})
    out["capital_deployed"] = out["open_count"] * TRADE_SIZE
    return out


def compute_monthly_cashflow(trades_df: pd.DataFrame, as_of) -> pd.DataFrame:
    """Per calendar month: how much fresh capital new entries need, how much exits free up,
    and the running balance if you start at zero and only reinvest what's freed (pure rotation)."""
    if trades_df.empty:
        return pd.DataFrame(columns=["month", "entries_count", "capital_needed", "exits_count", "capital_freed", "net_flow", "cumulative_balance"])

    df = trades_df.copy()
    df["entry_date"] = pd.to_datetime(df["entry_date"])
    entry_month = df["entry_date"].dt.to_period("M")
    entries = entry_month.value_counts().rename("entries_count")

    wins = df[df["status"] == "WIN"].copy()
    wins["exit_date"] = pd.to_datetime(wins["exit_date"])
    exit_month = wins["exit_date"].dt.to_period("M")
    exits = exit_month.value_counts().rename("exits_count") if not exit_month.empty else pd.Series(dtype=int)

    last_month = pd.Timestamp(as_of).to_period("M")
    all_months = pd.period_range(entry_month.min(), max(entry_month.max(), last_month), freq="M")
    monthly = pd.DataFrame(index=all_months)
    monthly["entries_count"] = entries.reindex(all_months, fill_value=0)
    monthly["exits_count"] = exits.reindex(all_months, fill_value=0)
    monthly["capital_needed"] = monthly["entries_count"] * TRADE_SIZE
    monthly["capital_freed"] = monthly["exits_count"] * TRADE_SIZE
    monthly["net_flow"] = monthly["capital_freed"] - monthly["capital_needed"]
    monthly["cumulative_balance"] = monthly["net_flow"].cumsum()
    monthly = monthly.reset_index().rename(columns={"index": "month"})
    monthly["month"] = monthly["month"].astype(str)
    return monthly


def analyze_cashflow(trades_df: pd.DataFrame, as_of=None) -> CashFlowResult:
    if trades_df.empty:
        return CashFlowResult(ok=False, message="No trades to analyze - run the optimizer first.")
    if as_of is None:
        prices = base.read_daily_prices()
        as_of = prices["date"].max()

    daily = compute_overlap_timeline(trades_df, as_of)
    monthly = compute_monthly_cashflow(trades_df, as_of)

    peak_idx = daily["open_count"].idxmax()
    peak_concurrent = int(daily.loc[peak_idx, "open_count"])
    peak_capital = float(daily.loc[peak_idx, "capital_deployed"])
    peak_date = daily.loc[peak_idx, "date"]
    avg_concurrent = float(daily["open_count"].mean())
    min_cum = float(monthly["cumulative_balance"].min()) if not monthly.empty else 0.0

    message = (
        f"Peak {peak_concurrent} concurrent open tranches on {peak_date.date()} "
        f"(Rs.{peak_capital:,.0f} deployed at once). Average concurrency: {avg_concurrent:.1f} tranches "
        f"(~Rs.{avg_concurrent * TRADE_SIZE:,.0f})."
    )
    return CashFlowResult(
        ok=True, message=message, daily=daily, monthly=monthly,
        peak_concurrent=peak_concurrent, peak_capital=peak_capital, peak_date=peak_date,
        avg_concurrent=avg_concurrent, min_cumulative_balance=min_cum,
    )


# --------------------------------------------------------------------------
# Affordability simulator: given real starting capital + a monthly salary
# infusion, walk every historical entry/exit chronologically and see which
# trades you could actually have afforded to take, vs. which you'd have had
# to skip for lack of cash - and what that skipped upside cost you.
# --------------------------------------------------------------------------

@dataclass
class AffordabilityResult:
    ok: bool
    message: str = ""
    balance_timeline: pd.DataFrame = field(default_factory=pd.DataFrame)  # date, cash_balance
    trades: pd.DataFrame = field(default_factory=pd.DataFrame)  # original trades + taken/skipped flag
    taken_count: int = 0
    skipped_count: int = 0
    missed_gain: float = 0.0


def simulate_affordability(trades_df: pd.DataFrame, starting_capital: float, monthly_infusion: float, as_of=None) -> AffordabilityResult:
    if trades_df.empty:
        return AffordabilityResult(ok=False, message="No trades to simulate - run the optimizer first.")
    if as_of is None:
        prices = base.read_daily_prices()
        as_of = prices["date"].max()
    as_of = pd.Timestamp(as_of)

    df = trades_df.reset_index(drop=True).copy()
    df["entry_date"] = pd.to_datetime(df["entry_date"])
    df["end_date"] = _effective_end_date(df, as_of)

    events = []
    for idx, row in df.iterrows():
        events.append((row["entry_date"], 0, "ENTRY", idx))
    for idx, row in df.iterrows():
        if row["status"] == "WIN":
            events.append((row["end_date"], 1, "EXIT", idx))
    # process same-day EXIT before ENTRY so freed cash can fund a same-day new entry
    events.sort(key=lambda e: (e[0], -e[1]))

    cash = float(starting_capital)
    current_month = df["entry_date"].min().to_period("M")
    taken = pd.Series(False, index=df.index)
    balance_rows = [(df["entry_date"].min() - pd.Timedelta(days=1), cash)]

    for event_date, _order, kind, idx in events:
        event_month = event_date.to_period("M")
        while current_month < event_month:
            current_month += 1
            cash += monthly_infusion
        if kind == "EXIT":
            if taken[idx]:
                row = df.loc[idx]
                cash += TRADE_SIZE * (1 + row["return_pct"] / 100)
        else:  # ENTRY
            if cash >= TRADE_SIZE:
                cash -= TRADE_SIZE
                taken[idx] = True
        balance_rows.append((event_date, cash))

    df["taken"] = taken
    df["afford_status"] = np.where(df["taken"], "TAKEN", "SKIPPED (insufficient cash)")

    balance_df = pd.DataFrame(balance_rows, columns=["date", "cash_balance"]).drop_duplicates("date", keep="last")
    skipped = df[~df["taken"]]
    # only WIN trades have a realized return to have "missed"; PENDING skipped trades have no known outcome yet
    missed_gain = (skipped[skipped["status"] == "WIN"]["return_pct"] / 100 * TRADE_SIZE).sum()

    message = (
        f"{int(taken.sum())} of {len(df)} trades affordable, {int((~taken).sum())} skipped for lack of cash "
        f"(missed ~Rs.{missed_gain:,.0f} of realized gains from skipped winners)."
    )
    return AffordabilityResult(
        ok=True, message=message, balance_timeline=balance_df, trades=df,
        taken_count=int(taken.sum()), skipped_count=int((~taken).sum()), missed_gain=float(missed_gain),
    )
