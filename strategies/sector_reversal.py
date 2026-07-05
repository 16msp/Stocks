"""
NSE sector ETF reversal screen.

Combines same-sector ETFs from different providers (see sector_groups.py) into
one equal-weighted synthetic sector index, using the same daily price/volume
history nse_etf_momentum.fetch() already stores locally (no separate fetch
needed here - this is a pure analysis layer on top of that data).

For each sector, over the last N+1 weekly buckets:
  - build an equal-weighted daily return series across its constituent ETFs,
    compound it into a sector index (base 100)
  - sum daily volume across constituents (total sector liquidity)
  - bucket into ISO weeks, compute week-over-week % changes

"Reversing" = the sector was net down over the weeks before the most recent
one, and the most recent week turned positive - a falling sector that just
started turning up.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from strategies import nse_etf_momentum as base
from strategies.sector_groups import SECTOR_GROUPS


@dataclass
class SectorReversalResult:
    ok: bool
    message: str = ""
    summary: pd.DataFrame = field(default_factory=pd.DataFrame)
    chart_long: pd.DataFrame = field(default_factory=pd.DataFrame)  # sector, week_key, rebased_index
    missing_symbols: dict = field(default_factory=dict)  # sector -> [symbols not found in stored data]
    week_keys: list = field(default_factory=list)


def sector_daily_index(prices: pd.DataFrame, symbols: list[str]) -> pd.DataFrame | None:
    """Equal-weighted synthetic daily index for a sector's constituent ETFs.

    Returns columns: date, index_close, index_high, volume.
    index_high approximates the sector's day-high each day (average of each
    constituent's day-high vs. its own prior close) - used to detect a price
    "touch" of a target during a day, not just the closing level. Day-high is
    max(open, close), not the raw intraday high - a fleeting intraday tick
    isn't reliably tradable, but the open and close are real, settled prices.
    Shared by the live sector-reversal screen and the backtest engine.
    """
    sub = prices[prices["symbol"].isin(symbols)].copy()
    if sub.empty:
        return None
    sub = sub.sort_values(["symbol", "date"])
    sub["day_high"] = sub[["open", "close"]].max(axis=1)
    sub["prev_close"] = sub.groupby("symbol")["close"].shift(1)
    sub["ret"] = sub["close"] / sub["prev_close"] - 1
    sub["high_ret"] = sub["day_high"] / sub["prev_close"] - 1

    per_day = (
        sub.groupby("date")
        .agg(avg_ret=("ret", "mean"), avg_high_ret=("high_ret", "mean"), volume=("volume", "sum"))
        .reset_index()
        .sort_values("date")
        .reset_index(drop=True)
    )
    per_day["avg_ret"] = per_day["avg_ret"].fillna(0)
    per_day["index_close"] = 100 * (1 + per_day["avg_ret"]).cumprod()
    prev_index = per_day["index_close"].shift(1).fillna(100.0)
    per_day["index_high"] = prev_index * (1 + per_day["avg_high_ret"].fillna(per_day["avg_ret"]))
    return per_day[["date", "index_close", "index_high", "volume"]]


def _sector_weekly_series(prices: pd.DataFrame, symbols: list[str]) -> pd.DataFrame | None:
    per_day = sector_daily_index(prices, symbols)
    if per_day is None:
        return None
    per_day = base.add_week_key(per_day)
    weekly = (
        per_day.groupby("week_key")
        .agg(week_close=("index_close", "last"), week_volume=("volume", "sum"), last_date=("date", "max"))
        .reset_index()
        .sort_values("last_date")
    )
    return weekly


def analyze(weeks: int = 4, min_avg_volume: int = 0) -> SectorReversalResult:
    prices = base.read_daily_prices()
    if prices.empty:
        return SectorReversalResult(ok=False, message="No stored data yet. Run fetch first (see NSE ETF Weekly Momentum page).")

    prices, _bad_ticks = base.drop_bad_ticks(prices)
    stored_symbols = set(prices["symbol"].unique())

    rows = []
    chart_rows = []
    missing_symbols = {}
    all_week_keys: list[str] = []

    for sector, symbols in SECTOR_GROUPS.items():
        present = [s for s in symbols if s in stored_symbols]
        missing = [s for s in symbols if s not in stored_symbols]
        if missing:
            missing_symbols[sector] = missing
        if not present:
            continue

        weekly = _sector_weekly_series(prices, present)
        if weekly is None or len(weekly) < 2:
            continue

        window = weekly.tail(weeks + 1).reset_index(drop=True)
        if len(window) < 2:
            continue

        week_keys = window["week_key"].tolist()
        all_week_keys = week_keys if len(week_keys) > len(all_week_keys) else all_week_keys
        closes = window["week_close"].tolist()
        volumes = window["week_volume"].tolist()

        changes = [(closes[i] - closes[i - 1]) / closes[i - 1] * 100 for i in range(1, len(closes))]
        latest_change_pct = changes[-1]
        prior_cumulative_pct = (
            (closes[-2] - closes[0]) / closes[0] * 100 if len(closes) >= 3 else None
        )
        avg_weekly_volume = sum(volumes) / len(volumes)
        latest_volume_change_pct = (
            (volumes[-1] - volumes[-2]) / volumes[-2] * 100 if len(volumes) >= 2 and volumes[-2] else None
        )

        reversing = prior_cumulative_pct is not None and prior_cumulative_pct < 0 and latest_change_pct > 0

        rows.append(
            {
                "sector": sector,
                "etfs_used": len(present),
                "etfs_total": len(symbols),
                "weeks_available": len(changes),
                "prior_weeks_change_pct": prior_cumulative_pct,
                "latest_week_change_pct": latest_change_pct,
                "latest_week_volume_change_pct": latest_volume_change_pct,
                "avg_weekly_volume": avg_weekly_volume,
                "reversing": reversing,
            }
        )

        base_close = closes[0]
        for wk, close in zip(week_keys, closes):
            chart_rows.append({"sector": sector, "week_key": wk, "rebased_index": close / base_close * 100})

    if not rows:
        return SectorReversalResult(ok=False, message="Not enough stored history yet to compute sector trends. Fetch more data / wait for another weekly run.")

    summary = pd.DataFrame(rows)
    if min_avg_volume:
        summary = summary[summary["avg_weekly_volume"] >= min_avg_volume]
    summary = summary.sort_values(
        ["reversing", "prior_weeks_change_pct"], ascending=[False, True]
    ).reset_index(drop=True)

    chart_long = pd.DataFrame(chart_rows)

    return SectorReversalResult(
        ok=True,
        message=f"{len(summary)} sectors analyzed over the last {weeks} week(s).",
        summary=summary,
        chart_long=chart_long,
        missing_symbols=missing_symbols,
        week_keys=all_week_keys,
    )
