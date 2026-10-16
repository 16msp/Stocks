"""Favourite stocks ("further analysis" list), kept in the same database as the price store.

SQLite locally, Postgres (Supabase) when DATABASE_URL is set, so the list survives restarts and
is the same on every device. The Streamlit pages read it once per session and keep a copy in
session_state, so rendering a card never hits the database.
"""

from __future__ import annotations

from datetime import datetime

import pandas as pd

from strategies import price_store

COLUMNS = ["Stock", "Sector", "Note", "Added"]


def _db():
    db = price_store._connect()
    db.execute(
        """CREATE TABLE IF NOT EXISTS favourites (
            symbol TEXT PRIMARY KEY, sector TEXT, note TEXT, added_at TEXT)"""
    )
    db.commit()
    return db


def load() -> pd.DataFrame:
    """All favourites, newest first."""
    db = _db()
    try:
        rows = db.rows("SELECT symbol, sector, COALESCE(note, ''), added_at FROM favourites ORDER BY added_at DESC")
    finally:
        db.close()
    return pd.DataFrame(rows, columns=COLUMNS)


def add(symbol: str, sector: str) -> None:
    db = _db()
    try:
        db.execute(
            "INSERT INTO favourites (symbol, sector, note, added_at) VALUES (?, ?, '', ?) "
            "ON CONFLICT (symbol) DO NOTHING",
            (symbol, sector, datetime.now().isoformat(timespec="seconds")),
        )
        db.commit()
    finally:
        db.close()


def remove(symbols) -> None:
    symbols = list(symbols)
    if not symbols:
        return
    db = _db()
    try:
        for sym in symbols:
            db.execute("DELETE FROM favourites WHERE symbol = ?", (sym,))
        db.commit()
    finally:
        db.close()


def set_notes(notes: dict) -> None:
    """notes = {symbol: text}"""
    if not notes:
        return
    db = _db()
    try:
        for sym, text in notes.items():
            db.execute("UPDATE favourites SET note = ? WHERE symbol = ?", (text or "", sym))
        db.commit()
    finally:
        db.close()
