"""Dip watchlist: a short list of good, growth-oriented stocks picked by hand, tracked by how far each is below its all-time closing high.

The idea is to buy more of a stock you believe in when it is far below its high and to sell when it recovers, so the list only
times the entry: choosing a good business is the user's job. Saved in the shared database (SQLite locally, Postgres on the hosted app),
changed only when the user adds, removes or saves a note.
"""

from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd

from strategies import price_store

COLUMNS = ["Stock", "Note", "Added"]

# (drop from the all-time closing high at which the zone starts, name, what it means)
ZONES = [
    (0.55, "Very deep", "More than 55% below the all-time high. Historically the biggest recoveries, but also where businesses that really broke sit: check why it fell."),
    (0.45, "Deep buy zone", "45% to 55% below. In the backtest about 6 in 10 got back to the old high within 3 years."),
    (0.35, "Buy zone", "35% to 45% below. The first level where buying beat the average stock in the backtest."),
    (0.25, "Watch", "25% to 35% below. In the backtest, buying here did no better than the average stock."),
    (0.15, "Pullback", "15% to 25% below."),
    (0.00, "Near high", "Within 15% of the all-time high."),
]

# Backtest of the rule on 781 stocks (2015 to Oct 2026), traded value of at least 25 crore a day, first day the drop crossed each level.
BACKTEST_ROWS = [
    {"Drop from all-time high": "25%", "Events": 1731, "Median after 12 months": 4.5, "Median after 24 months": 19.8, "Positive after 24 months": 64, "Back to the old high within 3 years": 77},
    {"Drop from all-time high": "35%", "Events": 1246, "Median after 12 months": 8.1, "Median after 24 months": 30.8, "Positive after 24 months": 69, "Back to the old high within 3 years": 66},
    {"Drop from all-time high": "45%", "Events": 812, "Median after 12 months": 13.8, "Median after 24 months": 39.7, "Positive after 24 months": 70, "Back to the old high within 3 years": 60},
    {"Drop from all-time high": "55%", "Events": 490, "Median after 12 months": 17.3, "Median after 24 months": 35.1, "Positive after 24 months": 71, "Back to the old high within 3 years": 52},
]


def zone_of(drop: float) -> str:
    """Zone name for a drop from the all-time high (a positive fraction, 0.4 = 40% below)."""
    if drop is None or pd.isna(drop):
        return "n/a"
    for start, name, _ in ZONES:
        if drop >= start:
            return name
    return "Near high"


def _db():
    db = price_store._connect()
    db.execute("CREATE TABLE IF NOT EXISTS dip_watchlist (symbol TEXT PRIMARY KEY, note TEXT, added_at TEXT)")
    db.commit()
    return db


def load() -> pd.DataFrame:
    """The watchlist, newest first."""
    db = _db()
    try:
        rows = db.rows("SELECT symbol, COALESCE(note, ''), added_at FROM dip_watchlist ORDER BY added_at DESC")
    finally:
        db.close()
    return pd.DataFrame(rows, columns=COLUMNS)


def add(symbols) -> int:
    """Add stocks the user chose; ones already on the list are left alone. Returns how many were new."""
    symbols = sorted({s for s in symbols if isinstance(s, str) and s})
    if not symbols:
        return 0
    db = _db()
    try:
        have = {r[0] for r in db.rows("SELECT symbol FROM dip_watchlist")}
        now = datetime.now().isoformat(timespec="seconds")
        for s in symbols:
            db.execute("INSERT INTO dip_watchlist (symbol, note, added_at) VALUES (?, '', ?) ON CONFLICT (symbol) DO NOTHING", (s, now))
        db.commit()
    finally:
        db.close()
    return len([s for s in symbols if s not in have])


def remove(symbols) -> None:
    symbols = list(symbols)
    if not symbols:
        return
    db = _db()
    try:
        for s in symbols:
            db.execute("DELETE FROM dip_watchlist WHERE symbol = ?", (s,))
        db.commit()
    finally:
        db.close()


def set_notes(notes: dict) -> None:
    """notes = {symbol: text}"""
    if not notes:
        return
    db = _db()
    try:
        for s, text in notes.items():
            db.execute("UPDATE dip_watchlist SET note = ? WHERE symbol = ?", (text or "", s))
        db.commit()
    finally:
        db.close()


def status(history: pd.DataFrame | None, ath: tuple | None) -> dict | None:
    """Where one stock stands against its all-time closing high.

    `ath` is the (price, date) kept by price_store.update_ath, or None; the stored sessions can only raise it (a new high since the last weekly refresh).
    Without a stored all-time high the highest stored close is used and `ath_known` is False (the stored history is only about 15 months).
    """
    if history is None or history.empty:
        return None
    close = history["Close"].dropna()
    if close.empty:
        return None
    last = float(close.iloc[-1])
    top, top_date, known = float(close.max()), close.idxmax().date(), False
    if ath:
        known = True
        if ath[0] >= top:
            top, top_date = float(ath[0]), pd.to_datetime(ath[1]).date()
    drop = 1.0 - last / top
    week_ago = float(close.iloc[-6]) if len(close) >= 6 else np.nan
    return {
        "CMP": last, "ATH": top, "ATH date": top_date, "ath_known": known, "Drop from ATH": drop, "Zone": zone_of(drop),
        "Drop 1W ago": 1.0 - week_ago / top if not np.isnan(week_ago) else np.nan,
        "Off 52W low": last / float(close.iloc[-252:].min()) - 1.0,
        "vs 200DMA": last / float(close.iloc[-200:].mean()) - 1.0 if len(close) >= 200 else np.nan,
        "Last session": close.index[-1].date(),
    }
