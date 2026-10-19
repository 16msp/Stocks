"""Favourite stocks ("further analysis" list), kept in the same database as the price store.

SQLite locally, Postgres (Supabase) when DATABASE_URL is set, so the list survives restarts and
is the same on every device. The Streamlit pages read it once per session and keep a copy in
session_state, so rendering a card never hits the database.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd

from strategies import price_store

COLUMNS = ["Stock", "Sector", "Note", "Added"]
MAX_ADDS_PER_MINUTE = 5  # one-by-one adds (card hearts) beyond this are refused: a person cannot click that fast, a runaway can


class TooFast(Exception):
    """Too many favourites added within a minute."""


def _who() -> tuple:
    """(session id, browser user-agent) of the viewer who triggered this, for the log; blanks outside a Streamlit session."""
    try:
        import streamlit as st
        from streamlit.runtime.scriptrunner import get_script_run_ctx

        ctx = get_script_run_ctx()
        return (ctx.session_id[:8] if ctx else ""), str(st.context.headers.get("User-Agent", ""))[:200]
    except Exception:
        return "", ""


def _log(db, action: str, symbol: str, source: str) -> None:
    """One row per add / remove / refused add: when (UTC), what, which stock, which page and card it came from, and the viewer's session and browser."""
    try:
        session, agent = _who()
        db.execute("INSERT INTO favourite_log (at, action, symbol, source, session, agent) VALUES (?, ?, ?, ?, ?, ?)",
                   (datetime.now(timezone.utc).isoformat(timespec="seconds"), action, symbol, source, session, agent))
    except Exception:  # the log must never stop a favourite being saved
        pass


def _db():
    db = price_store._connect()
    db.execute(
        """CREATE TABLE IF NOT EXISTS favourites (
            symbol TEXT PRIMARY KEY, sector TEXT, note TEXT, added_at TEXT)"""
    )
    db.execute("CREATE TABLE IF NOT EXISTS favourite_log (at TEXT, action TEXT, symbol TEXT, source TEXT, session TEXT, agent TEXT)")
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


def add(symbol: str, sector: str, source: str = "") -> None:
    """Add one favourite (a card heart). Refused with TooFast after MAX_ADDS_PER_MINUTE adds in the last minute."""
    db = _db()
    try:
        since = (datetime.now(timezone.utc) - timedelta(seconds=60)).isoformat(timespec="seconds")
        recent = db.rows("SELECT count(*) FROM favourite_log WHERE action = 'add' AND at >= ?", (since,))[0][0]
        if recent >= MAX_ADDS_PER_MINUTE:
            _log(db, "refused", symbol, source)
            db.commit()
            raise TooFast(f"{recent} favourites were added in the last minute (limit {MAX_ADDS_PER_MINUTE}), so this one was ignored. Wait a minute and try again.")
        db.execute(
            "INSERT INTO favourites (symbol, sector, note, added_at) VALUES (?, ?, '', ?) "
            "ON CONFLICT (symbol) DO NOTHING",
            (symbol, sector, datetime.now().isoformat(timespec="seconds")),
        )
        _log(db, "add", symbol, source)
        db.commit()
    finally:
        db.close()


def add_many(pairs, source: str = "") -> None:
    """Add several favourites ticked on purpose in a table and saved with one button; pairs = [(symbol, sector)]. Not rate-limited, but logged."""
    pairs = list(pairs)
    if not pairs:
        return
    db = _db()
    try:
        now = datetime.now().isoformat(timespec="seconds")
        for symbol, sector in pairs:
            db.execute("INSERT INTO favourites (symbol, sector, note, added_at) VALUES (?, ?, '', ?) ON CONFLICT (symbol) DO NOTHING", (symbol, sector, now))
            _log(db, "add-batch", symbol, source)  # not "add": a saved batch must not use up the one-by-one limit
        db.commit()
    finally:
        db.close()


def remove(symbols, source: str = "") -> None:
    symbols = list(symbols)
    if not symbols:
        return
    db = _db()
    try:
        for sym in symbols:
            db.execute("DELETE FROM favourites WHERE symbol = ?", (sym,))
            _log(db, "remove", sym, source)
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
