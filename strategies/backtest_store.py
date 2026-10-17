"""Keeps Minervini backtest runs in the shared database (SQLite locally, Postgres when hosted).

bt_runs   one row per run: when, the settings and the period covered
bt_trades one row per signal of a run: the setup (entry, stop, target, base shape, market state) and how it ended
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

import pandas as pd

from strategies import price_store

KEEP_RUNS = 3

TRADE_COLUMNS = [
    "symbol", "sector", "signal_date", "nifty_state", "breadth50", "breadth200", "rs_rating", "pivot", "entry", "stop", "target", "risk_pct", "trade_check", "base_weeks",
    "base_depth", "contractions", "shrinking", "tightening", "volume_dried_up", "breakout_quality", "breakout_vol_x", "avg_value_cr",
    "exit_date", "exit_price", "outcome", "days_held", "gap_exit", "same_day", "max_gain", "max_drop", "r_multiple", "pct_return",
]
_TEXT = {"symbol", "sector", "signal_date", "nifty_state", "trade_check", "contractions", "exit_date", "outcome"}
_INT = {"shrinking", "tightening", "volume_dried_up", "breakout_quality", "days_held", "gap_exit", "same_day"}


def _db():
    db = price_store._connect()
    real = "DOUBLE PRECISION" if db.is_pg else "REAL"
    cols = ", ".join(f"{c} {'TEXT' if c in _TEXT else 'INTEGER' if c in _INT else real}" for c in TRADE_COLUMNS)
    db.execute(
        f"""CREATE TABLE IF NOT EXISTS bt_runs (
            run_id TEXT PRIMARY KEY, created_at TEXT, params TEXT, first_date TEXT, last_date TEXT, stocks INTEGER, signals INTEGER)"""
    )
    db.execute(f"CREATE TABLE IF NOT EXISTS bt_trades (run_id TEXT NOT NULL, {cols}, PRIMARY KEY (run_id, symbol, signal_date))")
    # columns added after the first release: trades of older runs simply have no value
    if db.is_pg:
        for c in ("breadth50", "breadth200"):
            db.execute(f"ALTER TABLE bt_trades ADD COLUMN IF NOT EXISTS {c} {real}")
    else:
        have = [r[1] for r in db.rows("PRAGMA table_info(bt_trades)")]
        for c in ("breadth50", "breadth200"):
            if c not in have:
                db.execute(f"ALTER TABLE bt_trades ADD COLUMN {c} {real}")
    db.commit()
    return db


def save_run(trades: pd.DataFrame, params: dict, first_date: str, last_date: str, stocks: int) -> str:
    """Store a finished run (all its trades) and drop all but the newest KEEP_RUNS runs. Returns the run id."""
    run_id = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S") + "-" + uuid.uuid4().hex[:6]
    rows = []
    for rec in trades[TRADE_COLUMNS].to_dict("records"):
        rows.append((run_id, *[None if (isinstance(v, float) and pd.isna(v)) else (int(v) if c in _INT else v) for c, v in rec.items()]))
    db = _db()
    try:
        db.execute("INSERT INTO bt_runs (run_id, created_at, params, first_date, last_date, stocks, signals) VALUES (?, ?, ?, ?, ?, ?, ?)",
                   (run_id, datetime.now(timezone.utc).isoformat(timespec="seconds"), json.dumps(params), first_date, last_date, stocks, len(trades)))
        names = ", ".join(["run_id"] + TRADE_COLUMNS)
        if db.is_pg:
            from psycopg2.extras import execute_values

            cur = db.conn.cursor()
            execute_values(cur, f"INSERT INTO bt_trades ({names}) VALUES %s", rows, page_size=1000)
            cur.close()
        else:
            db.conn.executemany(f"INSERT INTO bt_trades ({names}) VALUES ({','.join('?' * (len(TRADE_COLUMNS) + 1))})", rows)
        old = [r[0] for r in db.rows("SELECT run_id FROM bt_runs ORDER BY created_at DESC")][KEEP_RUNS:]
        for rid in old:
            db.execute("DELETE FROM bt_trades WHERE run_id = ?", (rid,))
            db.execute("DELETE FROM bt_runs WHERE run_id = ?", (rid,))
        db.commit()
    finally:
        db.close()
    return run_id


def list_runs() -> pd.DataFrame:
    db = _db()
    try:
        return db.frame("SELECT run_id, created_at, params, first_date, last_date, stocks, signals FROM bt_runs ORDER BY created_at DESC")
    finally:
        db.close()


def load_trades(run_id: str) -> pd.DataFrame:
    db = _db()
    try:
        return db.frame(f"SELECT {', '.join(TRADE_COLUMNS)} FROM bt_trades WHERE run_id = ? ORDER BY signal_date, symbol", (run_id,))
    finally:
        db.close()
