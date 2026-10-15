"""
Daily-price store for stocks. SQLite file locally (same file as the ETF data), or
Postgres (e.g. Supabase) when a DATABASE_URL setting exists - that is how the hosted
app and the daily GitHub Actions job share one set of prices.

Everything the Position Guide and Sector Scanner compute (RS, volume profile,
Buy Score, ...) is derived from this store, so a daily rerun only downloads the
sessions that are new since the last run instead of 14 months of history.

How an update works, per symbol:
  - never seen        -> download ~15 months once
  - seen, up to date  -> nothing (last stored session is already the latest closed one,
                         or we checked in the last few hours - holidays/halts don't loop)
  - seen, behind      -> download from last stored session minus a 7-day overlap, upsert.
                         The overlap doubles as a corporate-action check: Yahoo's Close is
                         split/bonus-adjusted retroactively, so if the overlapping closes
                         disagree with what we stored, the stored history is stale and that
                         symbol is re-downloaded in full.
Only completed sessions are stored (today's partial bar is dropped).
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

from strategies.config import get_setting

DB_PATH = Path(__file__).resolve().parent.parent / "nse_etf_data.db"

CHUNK_SIZE = 50
FULL_HISTORY_DAYS = 460
OVERLAP_DAYS = 7
RECHECK_HOURS = 4
SPLIT_TOLERANCE = 0.005  # overlapping close differs by more than 0.5% -> history was re-adjusted
MARKET_CLOSE_IST = time(16, 0)
IST = timezone(timedelta(hours=5, minutes=30))
INTRADAY_RANGE_THRESHOLD = 0.35  # high/low this far from the day's close = bad print, ignored


class _Db:
    """Tiny wrapper so the store code is written once for SQLite and Postgres.
    SQL is written with `?` placeholders; they become `%s` for Postgres."""

    def __init__(self):
        url = get_setting("DATABASE_URL")
        self.is_pg = bool(url)
        if self.is_pg:
            import psycopg2

            self.conn = psycopg2.connect(url, connect_timeout=15)
        else:
            self.conn = sqlite3.connect(DB_PATH)
        real = "DOUBLE PRECISION" if self.is_pg else "REAL"
        self.execute(
            f"""CREATE TABLE IF NOT EXISTS stock_prices (
                symbol TEXT NOT NULL, date TEXT NOT NULL,
                open {real}, high {real}, low {real}, close {real}, volume {real},
                PRIMARY KEY (symbol, date))"""
        )
        self.execute(
            """CREATE TABLE IF NOT EXISTS stock_fetch_state (
                symbol TEXT PRIMARY KEY, first_date TEXT, last_date TEXT, checked_at TEXT)"""
        )
        self.commit()

    def _sql(self, sql: str) -> str:
        return sql.replace("?", "%s") if self.is_pg else sql

    def execute(self, sql: str, params=()):
        cur = self.conn.cursor()
        cur.execute(self._sql(sql), tuple(params))
        return cur

    def rows(self, sql: str, params=()) -> list:
        cur = self.execute(sql, params)
        out = cur.fetchall()
        cur.close()
        return out

    def frame(self, sql: str, params=()) -> pd.DataFrame:
        cur = self.execute(sql, params)
        cols = [d[0] for d in cur.description]
        out = pd.DataFrame(cur.fetchall(), columns=cols)
        cur.close()
        return out

    def upsert_prices(self, rows: list) -> None:
        if not rows:
            return
        sql = (
            "INSERT INTO stock_prices (symbol, date, open, high, low, close, volume) VALUES {} "
            "ON CONFLICT (symbol, date) DO UPDATE SET open = EXCLUDED.open, high = EXCLUDED.high, "
            "low = EXCLUDED.low, close = EXCLUDED.close, volume = EXCLUDED.volume"
        )
        if self.is_pg:
            from psycopg2.extras import execute_values

            cur = self.conn.cursor()
            execute_values(cur, sql.format("%s"), rows, page_size=1000)  # one round trip per 1000 rows
            cur.close()
        else:
            self.conn.executemany(sql.format("(?,?,?,?,?,?,?)"), rows)

    def commit(self):
        self.conn.commit()

    def close(self):
        self.conn.close()


def _connect() -> _Db:
    return _Db()


def yahoo_ticker(symbol: str) -> str:
    return symbol if symbol.startswith("^") else f"{symbol}.NS"


def expected_last_session(now: datetime | None = None) -> date:
    """Most recent weekday whose closing bar should be available (holidays not modelled -
    the recheck throttle handles those)."""
    now = (now or datetime.now(IST)).astimezone(IST)
    d = now.date()
    if d.weekday() >= 5 or now.time() < MARKET_CLOSE_IST:
        d -= timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def _download(symbols: list[str], start: date, end: date) -> dict[str, pd.DataFrame]:
    """One yfinance call for `symbols`; returns {symbol: OHLCV frame indexed by date}."""
    tickers = [yahoo_ticker(s) for s in symbols]
    try:
        data = yf.download(
            tickers, start=start.isoformat(), end=(end + timedelta(days=1)).isoformat(), interval="1d",
            group_by="ticker", threads=True, progress=False, auto_adjust=False,
        )
    except Exception:
        return {}
    if data is None or data.empty:
        return {}
    out = {}
    for sym, tk in zip(symbols, tickers):
        try:
            sub = data
            if isinstance(data.columns, pd.MultiIndex):
                # layout depends on yfinance version / ticker count: (ticker, field) or (field, ticker)
                if tk in data.columns.get_level_values(0):
                    sub = data[tk]
                elif tk in data.columns.get_level_values(1):
                    sub = data.xs(tk, axis=1, level=1)
            sub = sub[["Open", "High", "Low", "Close", "Volume"]].dropna(subset=["Close"])
        except (KeyError, TypeError):
            continue
        if not sub.empty:
            sub.index = pd.to_datetime(sub.index).tz_localize(None).normalize()
            out[sym] = sub
    return out


def _upsert(conn: _Db, symbol: str, frame: pd.DataFrame, replace: bool = False) -> None:
    if replace:
        conn.execute("DELETE FROM stock_prices WHERE symbol = ?", (symbol,))
    rows = [
        (symbol, ts.strftime("%Y-%m-%d"), _f(r.Open), _f(r.High), _f(r.Low), _f(r.Close), _f(r.Volume))
        for ts, r in frame.iterrows()
    ]
    conn.upsert_prices(rows)


def _f(x):
    return None if x is None or pd.isna(x) else float(x)


def _history_changed(conn: _Db, symbol: str, frame: pd.DataFrame) -> bool:
    """True if closes we already stored disagree with the freshly downloaded overlap."""
    stored = conn.frame(
        "SELECT date, close FROM stock_prices WHERE symbol = ? AND date >= ?",
        (symbol, frame.index.min().strftime("%Y-%m-%d")),
    )
    if stored.empty:
        return False
    stored["date"] = pd.to_datetime(stored["date"])
    merged = stored.set_index("date")["close"].to_frame("old").join(frame["Close"].rename("new"), how="inner")
    if merged.empty:
        return False
    return bool(((merged["new"] / merged["old"] - 1).abs() > SPLIT_TOLERANCE).any())


def update_prices(symbols: list[str], force: bool = False, chunk_size: int = CHUNK_SIZE) -> dict:
    """Bring the store up to date for `symbols`. Returns counts for display/diagnostics."""
    uniq = sorted({s for s in symbols if isinstance(s, str) and s})
    summary = {"requested": len(uniq), "new": 0, "incremental": 0, "up_to_date": 0, "resynced": 0, "no_data": 0}
    if not uniq:
        return summary

    expected = expected_last_session()
    now = datetime.now(IST)
    conn = _connect()
    try:
        state = {
            r[0]: (r[1], r[2])
            for r in conn.rows("SELECT symbol, last_date, checked_at FROM stock_fetch_state")
            if r[0] in set(uniq)
        }
        fresh_start = expected - timedelta(days=FULL_HISTORY_DAYS)
        new_syms, inc_syms = [], []
        for s in uniq:
            last, checked = state.get(s, (None, None))
            if last is None:
                new_syms.append(s)
                continue
            if not force:
                if date.fromisoformat(last) >= expected:
                    summary["up_to_date"] += 1
                    continue
                if checked and now - datetime.fromisoformat(checked) < timedelta(hours=RECHECK_HOURS):
                    summary["up_to_date"] += 1
                    continue
            inc_syms.append(s)

        def _clip(frame: pd.DataFrame) -> pd.DataFrame:
            return frame[frame.index.date <= expected]  # drop today's partial bar

        def _mark(symbol: str, frame: pd.DataFrame | None) -> None:
            first = conn.rows("SELECT MIN(date), MAX(date) FROM stock_prices WHERE symbol = ?", (symbol,))[0]
            conn.execute(
                "INSERT INTO stock_fetch_state (symbol, first_date, last_date, checked_at) VALUES (?,?,?,?) "
                "ON CONFLICT (symbol) DO UPDATE SET first_date = EXCLUDED.first_date, "
                "last_date = EXCLUDED.last_date, checked_at = EXCLUDED.checked_at",
                (symbol, first[0], first[1], now.isoformat()),
            )

        # --- never-seen symbols: one full download ---
        for i in range(0, len(new_syms), chunk_size):
            chunk = new_syms[i:i + chunk_size]
            got = _download(chunk, fresh_start, expected)
            for s in chunk:
                if s in got and not _clip(got[s]).empty:
                    _upsert(conn, s, _clip(got[s]))
                    summary["new"] += 1
                else:
                    summary["no_data"] += 1
                _mark(s, got.get(s))
            conn.commit()

        # --- known symbols that are behind: overlap download ---
        resync = []
        for i in range(0, len(inc_syms), chunk_size):
            chunk = inc_syms[i:i + chunk_size]
            start = min(date.fromisoformat(state[s][0]) for s in chunk) - timedelta(days=OVERLAP_DAYS)
            got = _download(chunk, start, expected)
            for s in chunk:
                frame = _clip(got[s]) if s in got else None
                if frame is None or frame.empty:
                    _mark(s, None)
                    summary["up_to_date"] += 1
                    continue
                if _history_changed(conn, s, frame):
                    resync.append(s)
                    continue
                _upsert(conn, s, frame)
                _mark(s, frame)
                summary["incremental"] += 1
            conn.commit()

        # --- corporate action detected: replace the symbol's history ---
        for i in range(0, len(resync), chunk_size):
            chunk = resync[i:i + chunk_size]
            got = _download(chunk, fresh_start, expected)
            for s in chunk:
                if s in got and not _clip(got[s]).empty:
                    _upsert(conn, s, _clip(got[s]), replace=True)
                    _mark(s, got[s])
                    summary["resynced"] += 1
                # on failure leave state untouched so the next run retries the resync
            conn.commit()
    finally:
        conn.close()
    return summary


def load_history(symbols: list[str]) -> dict[str, pd.DataFrame]:
    """{symbol: DataFrame[Open, High, Low, Close, Volume] indexed by date} from the store.
    High/Low far from that day's close are treated as bad prints and blanked (the day's
    close and volume are still used)."""
    uniq = sorted({s for s in symbols if isinstance(s, str) and s})
    if not uniq:
        return {}
    conn = _connect()
    try:
        q = f"SELECT * FROM stock_prices WHERE symbol IN ({','.join('?' * len(uniq))}) ORDER BY symbol, date"
        df = conn.frame(q, uniq)
    finally:
        conn.close()
    out = {}
    for sym, g in df.groupby("symbol"):
        g = g.drop(columns="symbol").assign(date=lambda d: pd.to_datetime(d["date"])).set_index("date")
        g.columns = [c.capitalize() for c in g.columns]
        bad_hi = g["High"] > g["Close"] * (1 + INTRADAY_RANGE_THRESHOLD)
        bad_lo = g["Low"] < g["Close"] * (1 - INTRADAY_RANGE_THRESHOLD)
        g.loc[bad_hi, "High"] = np.nan
        g.loc[bad_lo, "Low"] = np.nan
        out[sym] = g
    return out


def get_status(symbols: list[str]) -> dict:
    """Light summary of what's stored for these symbols (for a caption in the UI)."""
    uniq = sorted({s for s in symbols if isinstance(s, str) and s})
    if not uniq:
        return {}
    conn = _connect()
    try:
        q = f"SELECT COUNT(*), MAX(last_date), MIN(last_date), MAX(checked_at) FROM stock_fetch_state WHERE symbol IN ({','.join('?' * len(uniq))}) AND last_date IS NOT NULL"
        n, latest, oldest, checked = conn.rows(q, uniq)[0]
        rows = conn.rows(f"SELECT COUNT(*) FROM stock_prices WHERE symbol IN ({','.join('?' * len(uniq))})", uniq)[0][0]
    finally:
        conn.close()
    return {"symbols": n, "latest_session": latest, "oldest_latest": oldest, "checked_at": checked, "rows": rows,
            "backend": "Postgres (shared)" if get_setting("DATABASE_URL") else "local SQLite"}
