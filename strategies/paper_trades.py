"""Paper trading: pretend buys and sells recorded in the shared database (SQLite locally, Postgres when hosted).

Rules, kept deliberately simple:
- one buy spends at most BUDGET rupees: shares = floor(BUDGET / price), so the amount never goes above it;
- up to MAX_POSITIONS_PER_STOCK open positions per stock (each sells separately);
- trades execute at the last completed daily close from the price store (no live quotes);
- no brokerage, taxes or slippage; a sell closes the whole position.
"""

from __future__ import annotations

import uuid
from datetime import date

import pandas as pd

from strategies import price_store

BUDGET = 50_000  # rupees per transaction
MAX_POSITIONS_PER_STOCK = 3  # open positions allowed in one stock at a time

COLUMNS = ["id", "symbol", "sector", "qty", "buy_price", "buy_date", "buy_session", "sell_price", "sell_date", "sell_session"]


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
            sell_price {real}, sell_date TEXT, sell_session TEXT)"""
    )
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


def buy(symbol: str, sector: str, price: float, session: str | None) -> int:
    """Record a buy at `price`; returns the number of shares. Raises ValueError if even one share is too dear."""
    qty = quantity_for(price)
    if qty < 1:
        raise ValueError(f"{symbol} at Rs {price:,.2f} is more than the Rs {BUDGET:,} limit for one share.")
    db = _db()
    try:
        held = db.rows("SELECT COUNT(*) FROM paper_trades WHERE symbol = ? AND sell_price IS NULL", (symbol,))[0][0]
        if held >= MAX_POSITIONS_PER_STOCK:
            raise ValueError(f"You already hold {held} open positions in {symbol} (maximum {MAX_POSITIONS_PER_STOCK}). Sell one first.")
        db.execute(
            "INSERT INTO paper_trades (id, symbol, sector, qty, buy_price, buy_date, buy_session) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (uuid.uuid4().hex, symbol, sector, qty, round(float(price), 2), date.today().isoformat(), session),
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


def delete(trade_id: str) -> None:
    """Remove a mistaken trade entirely."""
    db = _db()
    try:
        db.execute("DELETE FROM paper_trades WHERE id = ?", (trade_id,))
        db.commit()
    finally:
        db.close()
