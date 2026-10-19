"""Paper trading: pretend buys and sells recorded in the shared database (SQLite locally, Postgres when hosted).

Rules, kept deliberately simple:
- one buy spends at most BUDGET rupees: shares = floor(BUDGET / price), so the amount never goes above it;
- up to MAX_POSITIONS_PER_STOCK open positions per stock (each sells separately);
- trades execute at the last completed daily close from the price store (no live quotes);
- no brokerage, taxes or slippage; a sell closes the whole position;
- a buy is refused if the same stock was bought less than SAME_STOCK_SECONDS ago or MAX_BUYS_PER_MINUTE buys were made in the last minute (a safeguard
  against runaway clicks; every new trade records when it was made);
- every buy also stores a snapshot of the card's technicals (SIGNAL_FIELDS) so that, months later, the trades can be
  compared with how they turned out.
"""

from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timedelta, timezone

import numpy as np
import pandas as pd

from strategies import price_store

BUDGET = 50_000  # rupees per transaction
MAX_POSITIONS_PER_STOCK = 3  # open positions allowed in one stock at a time
GENERAL, MINERVINI = "general", "minervini"  # which page a trade came from: the scanner/screener cards, or the Trend Template cards
SAME_STOCK_SECONDS = 60  # the same stock cannot be bought twice within this many seconds
MAX_BUYS_PER_MINUTE = 5  # across all stocks

COLUMNS = ["id", "symbol", "sector", "qty", "buy_price", "buy_date", "buy_session", "sell_price", "sell_date", "sell_session", "signals", "created_at", "strategy"]

# Scan-table column -> short label, in the order shown on the card. Numbers stay numbers; the text ones are labels.
SIGNAL_FIELDS = {
    "Recommendation": "Recommendation", "Buy Score": "Buy Score", "52W High Drop": "52W high drop", "ATH Drop": "ATH drop", "Public Δ 1Y": "Public holding change 1Y (pts)", "Off 52W Low": "Bounce off low",
    "RSI 14": "RSI", "RS 1M": "RS 1M", "RS 3M": "RS 3M", "vs 200DMA": "vs 200DMA", "Up/Down Vol": "Up/Down vol",
    "Vol Trend": "Vol trend", "Days Since Low": "Days since low", "Overhead Supply": "Overhead supply",
    "Supply to +25%": "Supply to +25%", "Volatility Ratio": "Volatility", "VP Zone": "Volume-profile zone",
    "vs POC": "vs POC", "Avg Value 20D": "Avg traded Cr/day", "Worst 1D Move": "Worst 1-day move", "Why": "Why (card text)",
}
TEXT_SIGNALS = {"Recommendation", "VP Zone", "Why"}


def snapshot(row) -> dict:
    """The card's technicals as a plain dict (NaN -> None) for storing with a buy. `row` is a scan-table row."""
    out = {}
    for col in SIGNAL_FIELDS:
        v = row.get(col) if hasattr(row, "get") else None
        if v is None or (not isinstance(v, str) and pd.isna(v)):
            out[col] = None
        elif col in TEXT_SIGNALS:
            out[col] = str(v)
        else:
            out[col] = round(float(v), 4)
    return out


PLAN_FIELDS = ["Status", "Trade check", "Market state", "Pivot", "Entry", "Stop", "Risk %", "Target 2R", "Target 3R", "Base weeks", "Base depth",
               "Contractions", "Shrinking", "Tightening", "Volume dried up", "Breakout quality", "Breadth 50", "Breadth 200"]


def plan_snapshot(plan) -> dict:
    """The Trend Template trade plan (pivot, stop, targets, status, market state...) as a plain dict for storing with a Minervini buy."""
    out = {}
    for k in PLAN_FIELDS:
        v = plan.get(k) if hasattr(plan, "get") else None
        if v is None or (not isinstance(v, (str, bool, np.bool_)) and pd.isna(v)):
            out[k] = None
        elif isinstance(v, (str, bool, np.bool_)):
            out[k] = v if isinstance(v, str) else bool(v)
        else:
            out[k] = round(float(v), 4)
    return out


def strategy_of(value) -> str:
    return MINERVINI if value == MINERVINI else GENERAL  # older trades have no value: they came from the scanner / screener / buy box


def parse_signals(text) -> dict:
    """Stored JSON back to a dict ({} for trades bought before snapshots existed, or unreadable text)."""
    if not isinstance(text, str) or not text:
        return {}
    try:
        data = json.loads(text)
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def quantity_for(price) -> int:
    """Whole shares that fit in BUDGET at this price (0 if the price is missing or above BUDGET)."""
    if price is None or pd.isna(price) or price <= 0:
        return 0
    return int(BUDGET // round(float(price), 2))  # same rounded price that gets stored, so the amount never exceeds BUDGET


def _db():
    db = price_store._connect()
    real = "DOUBLE PRECISION" if db.is_pg else "REAL"
    db.execute(
        f"""CREATE TABLE IF NOT EXISTS paper_trades (
            id TEXT PRIMARY KEY, symbol TEXT NOT NULL, sector TEXT, qty INTEGER NOT NULL,
            buy_price {real} NOT NULL, buy_date TEXT NOT NULL, buy_session TEXT,
            sell_price {real}, sell_date TEXT, sell_session TEXT, signals TEXT, created_at TEXT, strategy TEXT)"""
    )
    # columns added after the first release: trades made before them simply have no value
    if db.is_pg:
        db.execute("ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS signals TEXT")
        db.execute("ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS created_at TEXT")
        db.execute("ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS strategy TEXT")
    else:
        have = [r[1] for r in db.rows("PRAGMA table_info(paper_trades)")]
        for col in ("signals", "created_at", "strategy"):
            if col not in have:
                db.execute(f"ALTER TABLE paper_trades ADD COLUMN {col} TEXT")
    db.commit()
    return db


def load() -> pd.DataFrame:
    """Every trade, newest first. Open positions have no sell_price."""
    db = _db()
    try:
        rows = db.rows(f"SELECT {', '.join(COLUMNS)} FROM paper_trades ORDER BY buy_date DESC, id")
    finally:
        db.close()
    return pd.DataFrame(rows, columns=COLUMNS)


def open_counts() -> dict:
    """{symbol: number of open positions}"""
    db = _db()
    try:
        rows = db.rows("SELECT symbol, COUNT(*) FROM paper_trades WHERE sell_price IS NULL GROUP BY symbol")
    finally:
        db.close()
    return {sym: int(n) for sym, n in rows}


def buy(symbol: str, sector: str, price: float, session: str | None, signals: dict | None = None, strategy: str = GENERAL) -> int:
    """Record a buy at `price`, with the technicals snapshot; returns the number of shares. Raises ValueError if even one share is too dear."""
    qty = quantity_for(price)
    if qty < 1:
        raise ValueError(f"{symbol} at Rs {price:,.2f} is more than the Rs {BUDGET:,} limit for one share.")
    db = _db()
    try:
        held = db.rows("SELECT COUNT(*) FROM paper_trades WHERE symbol = ? AND sell_price IS NULL", (symbol,))[0][0]
        if held >= MAX_POSITIONS_PER_STOCK:
            raise ValueError(f"You already hold {held} open positions in {symbol} (maximum {MAX_POSITIONS_PER_STOCK}). Sell one first.")
        now = datetime.now(timezone.utc)
        same_cut = (now - timedelta(seconds=SAME_STOCK_SECONDS)).isoformat(timespec="seconds")
        if db.rows("SELECT COUNT(*) FROM paper_trades WHERE symbol = ? AND created_at >= ?", (symbol, same_cut))[0][0]:
            raise ValueError(f"{symbol} was bought less than {SAME_STOCK_SECONDS} seconds ago. Wait a minute before buying it again.")
        minute_cut = (now - timedelta(seconds=60)).isoformat(timespec="seconds")
        if db.rows("SELECT COUNT(*) FROM paper_trades WHERE created_at >= ?", (minute_cut,))[0][0] >= MAX_BUYS_PER_MINUTE:
            raise ValueError(f"{MAX_BUYS_PER_MINUTE} buys were already made in the last minute. Wait a moment, then try again.")
        db.execute(
            "INSERT INTO paper_trades (id, symbol, sector, qty, buy_price, buy_date, buy_session, signals, created_at, strategy) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (uuid.uuid4().hex, symbol, sector, qty, round(float(price), 2), date.today().isoformat(), session,
             json.dumps(signals) if signals else None, now.isoformat(timespec="seconds"), MINERVINI if strategy == MINERVINI else GENERAL),
        )
        db.commit()
    finally:
        db.close()
    return qty


def sell(trade_id: str, price: float, session: str | None) -> None:
    db = _db()
    try:
        db.execute(
            "UPDATE paper_trades SET sell_price = ?, sell_date = ?, sell_session = ? WHERE id = ? AND sell_price IS NULL",
            (round(float(price), 2), date.today().isoformat(), session, trade_id),
        )
        db.commit()
    finally:
        db.close()


def set_strategy(trade_id: str, strategy: str) -> None:
    """Move a trade to the other section."""
    db = _db()
    try:
        db.execute("UPDATE paper_trades SET strategy = ? WHERE id = ?", (MINERVINI if strategy == MINERVINI else GENERAL, trade_id))
        db.commit()
    finally:
        db.close()


def delete(trade_id: str) -> None:
    """Remove a mistaken trade entirely."""
    db = _db()
    try:
        db.execute("DELETE FROM paper_trades WHERE id = ?", (trade_id,))
        db.commit()
    finally:
        db.close()
