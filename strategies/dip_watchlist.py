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


# Turn signal, from the backtest of stocks 70%+ below their high (see BACKTEST_TURN_ROWS). Sessions = trading days.
BASE_SESSIONS = 120  # no new 52-week low for about 6 months
FALLING_SESSIONS = 20  # a new 52-week low within the last month
VOLUME_RISING = 1.2  # 50-day average volume against the 250-day average

# (stage, what it means), worst to best
STAGES = [
    ("Still falling", "Made a new 52-week low in the last 20 sessions. In the backtest the typical stock here was flat after a year and 1 in 5 lost another 30% or more."),
    ("Stabilising", "No new low for 1 to 6 months and still below the 200-day average. Too early: wait."),
    ("Base built", "No new low for 6 months or more but still below the 200-day average. Close to a turn: watch for the 200-day average."),
    ("Turning early", "Above the 200-day average, but the last low is under 6 months old. The base is still young."),
    ("Turning", "No new low for 6 months or more and back above the 200-day average. The best group in the backtest (median +53% after a year, 82% positive, 7% lost 30%+)."),
    ("Confirmed turn", "Turning, and volume is rising (50-day average at least 1.2x the 250-day average)."),
]

# Backtest on 781 stocks (2015 to Oct 2026), first day in each 60 days that a stock 70%+ below its high showed the pattern, entry next open, costs counted,
# Feb to Aug 2020 left out. The list holds only stocks that survived, so real results are weaker.
BACKTEST_TURN_ROWS = [
    {"Pattern": "Any stock 70%+ below its high", "Events": 399, "Median after 12 months": 19.6, "Lost 30%+": 16, "Positive after 12 months": 61},
    {"Pattern": "Made a new 52-week low in the last 20 sessions", "Events": 282, "Median after 12 months": -2.7, "Lost 30%+": 20, "Positive after 12 months": 48},
    {"Pattern": "Below the 200-day average, volume dried up", "Events": 294, "Median after 12 months": 14.7, "Lost 30%+": 18, "Positive after 12 months": 60},
    {"Pattern": "No new low for 120+ sessions", "Events": 272, "Median after 12 months": 40.6, "Lost 30%+": 12, "Positive after 12 months": 74},
    {"Pattern": "Above the 200-day average", "Events": 234, "Median after 12 months": 49.2, "Lost 30%+": 12, "Positive after 12 months": 79},
    {"Pattern": "Beating Nifty over 6 months", "Events": 266, "Median after 12 months": 46.8, "Lost 30%+": 12, "Positive after 12 months": 74},
    {"Pattern": "Up 50%+ from the 1-year low", "Events": 226, "Median after 12 months": 54.2, "Lost 30%+": 10, "Positive after 12 months": 77},
    {"Pattern": "Above the 200-day average and volume 1.2x its 250-day average", "Events": 204, "Median after 12 months": 46.2, "Lost 30%+": 8, "Positive after 12 months": 78},
    {"Pattern": "No new low for 120+ sessions and above the 200-day average (Turning)", "Events": 187, "Median after 12 months": 53.0, "Lost 30%+": 7, "Positive after 12 months": 82},
]


def stage_of(days_since_low, above_200, volume_ratio) -> str:
    """Turn-signal stage from the days since the 52-week low, the price against the 200-day average and the volume ratio."""
    if days_since_low is None or pd.isna(days_since_low) or above_200 is None or pd.isna(above_200):
        return "n/a"
    if days_since_low < FALLING_SESSIONS:
        return "Still falling"
    if days_since_low < BASE_SESSIONS:
        return "Stabilising" if not above_200 else "Turning early"
    if not above_200:
        return "Base built"
    if volume_ratio is not None and not pd.isna(volume_ratio) and volume_ratio >= VOLUME_RISING:
        return "Confirmed turn"
    return "Turning"


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


def status(history: pd.DataFrame | None, ath: tuple | None, nifty_close: pd.Series | None = None) -> dict | None:
    """Where one stock stands against its all-time closing high, and whether it has stopped falling and turned.

    `ath` is the (price, date) kept by price_store.update_ath, or None; the stored sessions can only raise it (a new high since the last weekly refresh).
    Without a stored all-time high the highest stored close is used and `ath_known` is False (the stored history is only about 15 months).
    `nifty_close` (Nifty's stored closes) gives the 6-month strength against the index.
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
    window = close.iloc[-252:]
    days_since_low = len(window) - 1 - int(np.flatnonzero(window.to_numpy() == window.min())[-1])
    dma200 = float(close.iloc[-200:].mean()) if len(close) >= 200 else np.nan
    vol = history["Volume"].reindex(close.index)
    volume_ratio = float(vol.iloc[-50:].mean() / vol.iloc[-250:].mean()) if vol.iloc[-250:].notna().sum() >= 200 and vol.iloc[-250:].mean() > 0 else np.nan
    rs6 = np.nan
    if nifty_close is not None and len(close) > 126:
        n = nifty_close.dropna()
        n = n.reindex(close.index, method="ffill").dropna() if len(n) else n
        if len(n) > 126 and n.index[-1] == close.index[-1]:
            rs6 = (last / float(close.iloc[-127]) - 1.0) - (float(n.iloc[-1]) / float(n.iloc[-127]) - 1.0)
    above_200 = bool(last > dma200) if not np.isnan(dma200) else None
    return {
        "CMP": last, "ATH": top, "ATH date": top_date, "ath_known": known, "Drop from ATH": drop, "Zone": zone_of(drop),
        "Drop 1W ago": 1.0 - week_ago / top if not np.isnan(week_ago) else np.nan,
        "Off 52W low": last / float(window.min()) - 1.0,
        "Days since low": days_since_low,
        "vs 200DMA": last / dma200 - 1.0 if not np.isnan(dma200) else np.nan,
        "Volume ratio": volume_ratio, "RS 6M": rs6,
        "Turn signal": stage_of(days_since_low, above_200, volume_ratio),
        "Last session": close.index[-1].date(),
    }
