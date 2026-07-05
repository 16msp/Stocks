"""
Live paper-trading tracker for the sector reversal strategy.

Runs the exact same rule the backtest validated (see sector_reversal_backtest.py)
against today's data: if a sector is flat (no open position) and its prior-weeks
decline exceeds the entry threshold while the latest week turned positive, open
a paper position. Every time this runs, open positions are checked against the
target - if touched, that's your sell alert. If held past the horizon without
hitting target, it's closed as a time-exit.

This is the only way to validate the strategy on sectors too new to backtest
(Defence, EV, Manufacturing, etc.) - by watching them live from here forward.

Positions are stored in the paper_trades table (schema created in
nse_etf_momentum.get_connection) so they persist across app restarts, and are
only updated - never re-created - each time this runs, so re-running never
double-counts a position.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime

import pandas as pd

from strategies import nse_etf_momentum as base
from strategies.sector_groups import SECTOR_GROUPS
from strategies.sector_reversal import sector_daily_index
from strategies.sector_reversal_backtest import DEFAULT_HORIZON_DAYS, DEFAULT_WEEKS, TARGET_PCT

DEFAULT_ENTRY_THRESHOLD = -10.0  # informed by backtest: best out-of-sample consistency (see Backtest page)


@dataclass
class LiveCheckResult:
    ok: bool
    message: str = ""
    newly_opened: pd.DataFrame = field(default_factory=pd.DataFrame)
    newly_closed: pd.DataFrame = field(default_factory=pd.DataFrame)  # the alerts
    open_positions: pd.DataFrame = field(default_factory=pd.DataFrame)


def get_open_positions() -> pd.DataFrame:
    conn = base.get_connection()
    try:
        return pd.read_sql_query("SELECT * FROM paper_trades WHERE status='OPEN'", conn)
    finally:
        conn.close()


def get_trade_history() -> pd.DataFrame:
    conn = base.get_connection()
    try:
        return pd.read_sql_query("SELECT * FROM paper_trades WHERE status != 'OPEN' ORDER BY exit_date DESC", conn)
    finally:
        conn.close()


def check_signals(
    entry_threshold: float = DEFAULT_ENTRY_THRESHOLD,
    weeks: int = DEFAULT_WEEKS,
    horizon_days: int = DEFAULT_HORIZON_DAYS,
    target_pct: float = TARGET_PCT,
) -> LiveCheckResult:
    prices = base.read_daily_prices()
    if prices.empty:
        return LiveCheckResult(ok=False, message="No stored data yet. Fetch data first.")
    prices, _bad = base.drop_bad_ticks(prices)
    stored_symbols = set(prices["symbol"].unique())

    conn = base.get_connection()
    try:
        open_df = pd.read_sql_query("SELECT * FROM paper_trades WHERE status='OPEN'", conn)
        open_sectors = set(open_df["sector"]) if not open_df.empty else set()

        newly_opened_rows = []
        newly_closed_rows = []
        today = date.today()

        for sector, symbols in SECTOR_GROUPS.items():
            present = [s for s in symbols if s in stored_symbols]
            if not present:
                continue
            daily = sector_daily_index(prices, present)
            if daily is None or daily.empty:
                continue
            latest_date = daily["date"].iloc[-1]
            latest_close = daily["index_close"].iloc[-1]

            if sector in open_sectors:
                row = open_df[open_df["sector"] == sector].iloc[0]
                entry_date = datetime.strptime(row["entry_date"], "%Y-%m-%d").date()
                days_held = (today - entry_date).days
                target_level = row["entry_index"] * (1 + row["target_pct"] / 100)

                since_entry = daily[daily["date"] > pd.Timestamp(row["entry_date"])]
                hit = since_entry[since_entry["index_high"] >= target_level]

                if not hit.empty:
                    exit_row = hit.iloc[0]
                    return_pct = (exit_row["index_high"] / row["entry_index"] - 1) * 100
                    conn.execute(
                        "UPDATE paper_trades SET status='WIN', exit_date=?, exit_index=?, exit_reason='target_hit', return_pct=? WHERE id=?",
                        (str(exit_row["date"].date()), float(exit_row["index_high"]), float(return_pct), int(row["id"])),
                    )
                    newly_closed_rows.append({**row.to_dict(), "exit_reason": "target_hit", "return_pct": return_pct})
                elif days_held > row["horizon_days"]:
                    return_pct = (latest_close / row["entry_index"] - 1) * 100
                    conn.execute(
                        "UPDATE paper_trades SET status='LOSS', exit_date=?, exit_index=?, exit_reason='time_exit', return_pct=? WHERE id=?",
                        (str(latest_date.date()), float(latest_close), float(return_pct), int(row["id"])),
                    )
                    newly_closed_rows.append({**row.to_dict(), "exit_reason": "time_exit", "return_pct": return_pct})
            else:
                per_day_week = base.add_week_key(daily)
                weekly = (
                    per_day_week.groupby("week_key")
                    .agg(week_close=("index_close", "last"), last_date=("date", "max"))
                    .reset_index()
                    .sort_values("last_date")
                )
                if len(weekly) <= weeks:
                    continue
                closes = weekly["week_close"].to_numpy()
                latest_change = (closes[-1] - closes[-2]) / closes[-2] * 100
                prior_cum = (closes[-2] - closes[-1 - weeks]) / closes[-1 - weeks] * 100

                if prior_cum <= entry_threshold and latest_change > 0:
                    conn.execute(
                        "INSERT INTO paper_trades (sector, entry_date, entry_index, entry_threshold, target_pct, horizon_days, status) "
                        "VALUES (?, ?, ?, ?, ?, ?, 'OPEN')",
                        (sector, str(latest_date.date()), float(latest_close), float(entry_threshold), float(target_pct), int(horizon_days)),
                    )
                    newly_opened_rows.append(
                        {"sector": sector, "entry_date": str(latest_date.date()), "entry_index": float(latest_close),
                         "prior_weeks_change_pct": prior_cum, "latest_week_change_pct": latest_change}
                    )

        conn.commit()
        open_now = pd.read_sql_query("SELECT * FROM paper_trades WHERE status='OPEN'", conn)
    finally:
        conn.close()

    if not open_now.empty:
        current_levels = {}
        for sector in open_now["sector"]:
            symbols = [s for s in SECTOR_GROUPS.get(sector, []) if s in stored_symbols]
            daily = sector_daily_index(prices, symbols)
            current_levels[sector] = daily["index_close"].iloc[-1] if daily is not None and not daily.empty else None
        open_now["current_index"] = open_now["sector"].map(current_levels)
        open_now["unrealized_pct"] = (open_now["current_index"] / open_now["entry_index"] - 1) * 100
        open_now["days_held"] = open_now["entry_date"].apply(lambda d: (today - datetime.strptime(d, "%Y-%m-%d").date()).days)

    n_open = len(newly_opened_rows)
    n_closed = len(newly_closed_rows)
    message = f"{n_open} new position(s) opened, {n_closed} closed (check alerts), {len(open_now)} still open."

    return LiveCheckResult(
        ok=True,
        message=message,
        newly_opened=pd.DataFrame(newly_opened_rows),
        newly_closed=pd.DataFrame(newly_closed_rows),
        open_positions=open_now,
    )
