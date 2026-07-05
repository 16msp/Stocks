"""
Historical backtest for the sector reversal strategy.

Rule being tested: for a sector that has been declining over the prior weeks
and then turns positive in the latest week, buy at the next session's open
(approximated here as the next trading day's close-index level) and hold,
watching every day, until either:
  - the sector index touches +TARGET_PCT% above entry at any point (WIN - this
    is when the app would alert you to sell), or
  - HORIZON_DAYS calendar days pass without that happening (time exit - loss).

Walks forward through the sector's full stored history (as far back as its
oldest constituent ETF goes - see nse_etf_momentum.backfill_history), so this
needs the deep historical backfill to have been run first, not just the
45-day window used for the live weekly screen.

Only sectors with enough backtestable history (see MIN_YEARS_FOR_BACKTEST)
are included - newer sectors (e.g. Defence, EV, Manufacturing) haven't
existed long enough to have lived through a single 6-8 month cycle yet, so a
historical backtest for them would be meaningless. Those are meant to be
tracked live instead (see sector_reversal_live.py).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from strategies import nse_etf_momentum as base
from strategies.sector_groups import SECTOR_GROUPS
from strategies.sector_reversal import sector_daily_index

DEFAULT_THRESHOLDS = [-3, -5, -8, -10, -15]  # % decline over the prior weeks, required to consider an entry
DEFAULT_WEEKS = 4
DEFAULT_HORIZON_DAYS = 240  # ~8 months
TARGET_PCT = 20.0
MIN_YEARS_FOR_BACKTEST = 2.0


@dataclass
class Trade:
    sector: str
    threshold: float
    signal_week: str
    entry_date: str
    entry_index: float
    status: str  # "WIN", "LOSS", "PENDING"
    exit_date: str | None = None
    exit_index: float | None = None
    days_held: int | None = None
    return_pct: float | None = None
    max_favorable_pct: float | None = None  # best it got, for LOSS trades - "how close did it get"


@dataclass
class BacktestResult:
    ok: bool
    message: str = ""
    trades: pd.DataFrame = field(default_factory=pd.DataFrame)
    per_sector_threshold: pd.DataFrame = field(default_factory=pd.DataFrame)
    pooled_by_threshold: pd.DataFrame = field(default_factory=pd.DataFrame)
    excluded_sectors: dict = field(default_factory=dict)  # sector -> reason


def _weekly_from_daily(daily: pd.DataFrame) -> pd.DataFrame:
    daily = base.add_week_key(daily)
    weekly = (
        daily.groupby("week_key")
        .agg(week_close=("index_close", "last"), last_date=("date", "max"))
        .reset_index()
        .sort_values("last_date")
        .reset_index(drop=True)
    )
    return weekly


def _simulate_sector(
    sector: str,
    daily: pd.DataFrame,
    weekly: pd.DataFrame,
    threshold: float,
    weeks: int,
    horizon_days: int,
) -> list[Trade]:
    closes = weekly["week_close"].to_numpy()
    last_dates = weekly["last_date"].to_numpy()
    week_keys = weekly["week_key"].tolist()
    n = len(closes)
    if n <= weeks:
        return []

    weekly_ret = np.full(n, np.nan)
    weekly_ret[1:] = (closes[1:] - closes[:-1]) / closes[:-1] * 100

    daily_dates = daily["date"].to_numpy()
    daily_close = daily["index_close"].to_numpy()
    daily_high = daily["index_high"].to_numpy()
    last_daily_date = daily_dates[-1]

    trades: list[Trade] = []
    flat_from_week = weeks  # index into weekly arrays; can't signal before this

    t = weeks
    while t < n:
        if t < flat_from_week:
            t += 1
            continue

        prior_cum = (np.prod(1 + weekly_ret[t - weeks + 1 : t] / 100) - 1) * 100
        latest = weekly_ret[t]

        if prior_cum <= threshold and latest > 0:
            # signal at week t -> enter at the next available daily bar after this week's last date
            entry_pos = np.searchsorted(daily_dates, last_dates[t], side="right")
            if entry_pos >= len(daily_dates):
                t += 1
                continue
            entry_date = daily_dates[entry_pos]
            entry_price = daily_close[entry_pos]
            target = entry_price * (1 + TARGET_PCT / 100)
            horizon_end = entry_date + np.timedelta64(horizon_days, "D")

            window_end_pos = np.searchsorted(daily_dates, horizon_end, side="right")
            window = daily_high[entry_pos + 1 : window_end_pos]
            hit_positions = np.nonzero(window >= target)[0]

            if hit_positions.size > 0:
                hit_pos = entry_pos + 1 + hit_positions[0]
                exit_date = daily_dates[hit_pos]
                days_held = int((exit_date - entry_date) / np.timedelta64(1, "D"))
                trades.append(
                    Trade(
                        sector=sector, threshold=threshold, signal_week=week_keys[t],
                        entry_date=str(entry_date)[:10], entry_index=float(entry_price),
                        status="WIN", exit_date=str(exit_date)[:10], exit_index=float(daily_high[hit_pos]),
                        days_held=days_held, return_pct=TARGET_PCT,
                    )
                )
                # sector goes flat again after exit; resume scanning from the week after exit_date
                resume_pos = np.searchsorted(daily_dates, exit_date, side="right")
            elif horizon_end <= last_daily_date:
                exit_pos = min(window_end_pos, len(daily_dates) - 1)
                exit_date = daily_dates[exit_pos]
                exit_price = daily_close[exit_pos]
                days_held = int((exit_date - entry_date) / np.timedelta64(1, "D"))
                max_favorable = (daily_high[entry_pos + 1 : exit_pos + 1].max() / entry_price - 1) * 100 if exit_pos > entry_pos else 0.0
                trades.append(
                    Trade(
                        sector=sector, threshold=threshold, signal_week=week_keys[t],
                        entry_date=str(entry_date)[:10], entry_index=float(entry_price),
                        status="LOSS", exit_date=str(exit_date)[:10], exit_index=float(exit_price),
                        days_held=days_held, return_pct=float((exit_price / entry_price - 1) * 100),
                        max_favorable_pct=float(max_favorable),
                    )
                )
                resume_pos = exit_pos + 1
            else:
                # horizon hasn't fully elapsed yet vs. available data - too recent to score
                trades.append(
                    Trade(
                        sector=sector, threshold=threshold, signal_week=week_keys[t],
                        entry_date=str(entry_date)[:10], entry_index=float(entry_price),
                        status="PENDING",
                    )
                )
                break  # nothing after this can resolve either; stop scanning this sector/threshold

            # advance to the first week strictly after we went flat again
            next_week_pos = np.searchsorted(last_dates, daily_dates[min(resume_pos, len(daily_dates) - 1)], side="left")
            t = max(t + 1, next_week_pos)
            continue

        t += 1

    return trades


def run_backtest(
    thresholds: list[float] = None,
    weeks: int = DEFAULT_WEEKS,
    horizon_days: int = DEFAULT_HORIZON_DAYS,
    min_years: float = MIN_YEARS_FOR_BACKTEST,
) -> BacktestResult:
    thresholds = thresholds or DEFAULT_THRESHOLDS
    prices = base.read_daily_prices()
    if prices.empty:
        return BacktestResult(ok=False, message="No stored data yet.")
    prices, _bad = base.drop_bad_ticks(prices)

    all_symbols = sorted({s for syms in SECTOR_GROUPS.values() for s in syms})
    depth = base.get_history_depth(all_symbols).set_index("symbol")["years_of_history"].to_dict()

    all_trades: list[Trade] = []
    excluded = {}

    for sector, symbols in SECTOR_GROUPS.items():
        present = [s for s in symbols if s in prices["symbol"].unique()]
        max_years = max((depth.get(s, 0.0) for s in present), default=0.0)
        if max_years < min_years:
            excluded[sector] = f"only {max_years:.1f}y of history (need {min_years}y+) - track live only"
            continue

        daily = sector_daily_index(prices, present)
        if daily is None or len(daily) < 30:
            excluded[sector] = "not enough daily data points"
            continue
        weekly = _weekly_from_daily(daily)

        for th in thresholds:
            all_trades.extend(_simulate_sector(sector, daily, weekly, th, weeks, horizon_days))

    if not all_trades:
        return BacktestResult(
            ok=False,
            message="No sectors had enough history to backtest. Run the deep historical backfill first.",
            excluded_sectors=excluded,
        )

    trades_df = pd.DataFrame([vars(t) for t in all_trades])

    resolved = trades_df[trades_df["status"].isin(["WIN", "LOSS"])]
    per_sector_threshold = (
        resolved.groupby(["sector", "threshold"])
        .agg(
            trades=("status", "count"),
            wins=("status", lambda s: (s == "WIN").sum()),
            avg_days_to_hit=("days_held", lambda s: resolved.loc[s.index][resolved.loc[s.index, "status"] == "WIN"]["days_held"].mean()),
            avg_loss_return_pct=("return_pct", lambda s: resolved.loc[s.index][resolved.loc[s.index, "status"] == "LOSS"]["return_pct"].mean()),
        )
        .reset_index()
    )
    per_sector_threshold["win_rate_pct"] = per_sector_threshold["wins"] / per_sector_threshold["trades"] * 100

    pooled = (
        resolved.groupby("threshold")
        .agg(trades=("status", "count"), wins=("status", lambda s: (s == "WIN").sum()))
        .reset_index()
    )
    pooled["win_rate_pct"] = pooled["wins"] / pooled["trades"] * 100
    win_days = resolved[resolved["status"] == "WIN"].groupby("threshold")["days_held"].mean()
    pooled["avg_days_to_hit"] = pooled["threshold"].map(win_days)

    n_pending = (trades_df["status"] == "PENDING").sum()
    n_sectors = trades_df["sector"].nunique()
    message = f"{len(resolved)} resolved trades across {n_sectors} backtestable sector(s); {n_pending} signal(s) too recent to have resolved yet."

    return BacktestResult(
        ok=True,
        message=message,
        trades=trades_df,
        per_sector_threshold=per_sector_threshold,
        pooled_by_threshold=pooled,
        excluded_sectors=excluded,
    )
