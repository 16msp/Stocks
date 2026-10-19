"""Quarterly shareholding pattern from NSE (promoter holding and public holding), kept in the shared database.

Each stock files a pattern 3 weeks after every quarter end. NSE gives about five years of them; they are stored in `stock_holding`
(one row per stock and quarter-end) and `stock_holding_check` (when a stock was last asked, so empty answers are not repeated every day).

What the tables show, all in percentage points of the company's shares:
- Public Holding  = public shareholding at the latest quarter end
- Public Δ 1Q / 1Y = how much it changed over the last quarter / the last year (negative = the public sold, so promoters or others bought)
Stocks with no promoter (banks, ITC, Eternal ...) report 0% promoter holding and a public share that is not "the rest", so they get no figures.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import requests

from strategies import price_store

BATCH = 40  # stocks written per database connection
HOLDING_REFRESH_DAYS = 14  # filings arrive once a quarter, results season is the only busy time
COLUMNS = ["Public Holding", "Public Δ 1Q", "Public Δ 1Y"]
_PAGE = "https://www.nseindia.com/companies-listing/corporate-filings-shareholding-pattern"
_API = "https://www.nseindia.com/api/corporate-share-holdings-master"
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "application/json,text/plain,*/*", "Referer": "https://www.nseindia.com/",
}


def _tables(conn) -> None:
    real = "DOUBLE PRECISION" if conn.is_pg else "REAL"
    conn.execute(f"CREATE TABLE IF NOT EXISTS stock_holding (symbol TEXT NOT NULL, quarter TEXT NOT NULL, promoter {real}, public {real}, broadcast TEXT, "
                 "PRIMARY KEY (symbol, quarter))")
    conn.execute("CREATE TABLE IF NOT EXISTS stock_holding_check (symbol TEXT PRIMARY KEY, checked_at TEXT)")
    conn.commit()


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update(_HEADERS)
    s.get(_PAGE, timeout=20)  # NSE only answers the API for a browser that has visited a page (cookies)
    return s


def parse(records: list) -> list[tuple]:
    """NSE's filing list -> [(quarter 'YYYY-MM-DD', promoter, public, broadcast 'YYYY-MM-DD')], quarter-end filings only, newest filing per quarter."""
    best = {}
    for r in records or []:
        q = pd.to_datetime(r.get("date"), format="%d-%b-%Y", errors="coerce")
        pro, pub = pd.to_numeric(r.get("pr_and_prgrp"), errors="coerce"), pd.to_numeric(r.get("public_val"), errors="coerce")
        if pd.isna(q) or pd.isna(pro) or pd.isna(pub) or q.month not in (3, 6, 9, 12) or q.day < 28:  # others are filings for events (IPO, listing ...)
            continue
        bc = pd.to_datetime(r.get("broadcastDate"), format="%d-%b-%Y %H:%M:%S", errors="coerce")
        bc = bc if not pd.isna(bc) else q + timedelta(days=45)
        key = q.strftime("%Y-%m-%d")
        if key not in best or bc >= best[key][3]:
            best[key] = (key, float(pro), float(pub), bc)
    return [(k, p, u, b.strftime("%Y-%m-%d")) for k, p, u, b in sorted(best.values())]


def update(symbols: list[str], max_age_days: int = HOLDING_REFRESH_DAYS, pause: float = 0.1) -> dict:
    """Ask NSE for the stocks never asked, or asked more than `max_age_days` ago. Returns counts. A stock NSE does not know is recorded as asked."""
    uniq = sorted({s for s in symbols if isinstance(s, str) and s and not s.startswith("^")})
    summary = {"requested": len(uniq), "updated": 0, "fresh": 0, "no_data": 0, "failed": 0}
    if not uniq:
        return summary
    now = datetime.now(timezone.utc)
    cutoff = (now - timedelta(days=max_age_days)).isoformat(timespec="seconds")
    conn = price_store._connect()
    try:
        _tables(conn)
        checked = {s: c for s, c in conn.rows("SELECT symbol, checked_at FROM stock_holding_check")}
    finally:
        conn.close()
    stale = [s for s in uniq if checked.get(s) is None or checked[s] < cutoff]
    summary["fresh"] = len(uniq) - len(stale)
    if not stale:
        return summary
    sess, failed_in_row, pending = _session(), 0, []

    def flush() -> None:
        """Write the stocks fetched so far over one connection (a connection to the hosted database is slow to open)."""
        if not pending:
            return
        for attempt in range(3):  # the hosted database drops a connection now and then
            try:
                _write(pending)
                break
            except Exception:
                if attempt == 2:
                    raise
                time.sleep(2 * (attempt + 1))
        pending.clear()

    def _write(batch) -> None:
        holding = [(sym, q, pro, pub, bc) for sym, rows in batch for q, pro, pub, bc in rows]
        checks = [(sym, now.isoformat(timespec="seconds")) for sym, _ in batch]
        h_sql = ("INSERT INTO stock_holding (symbol, quarter, promoter, public, broadcast) VALUES {} ON CONFLICT (symbol, quarter) "
                 "DO UPDATE SET promoter = EXCLUDED.promoter, public = EXCLUDED.public, broadcast = EXCLUDED.broadcast")
        c_sql = "INSERT INTO stock_holding_check (symbol, checked_at) VALUES {} ON CONFLICT (symbol) DO UPDATE SET checked_at = EXCLUDED.checked_at"
        conn = price_store._connect()
        try:
            _tables(conn)
            if conn.is_pg:  # one statement per batch: every round trip to the hosted database costs a second or so
                from psycopg2.extras import execute_values

                cur = conn.conn.cursor()
                if holding:
                    execute_values(cur, h_sql.format("%s"), holding)
                execute_values(cur, c_sql.format("%s"), checks)
                cur.close()
            else:
                if holding:
                    conn.conn.executemany(h_sql.format("(?, ?, ?, ?, ?)"), holding)
                conn.conn.executemany(c_sql.format("(?, ?)"), checks)
            conn.commit()
        finally:
            conn.close()

    try:
        for i, sym in enumerate(stale):
            if i and i % 150 == 0:
                sess = _session()  # NSE drops long sessions
            records = None
            for attempt in range(3):
                try:
                    resp = sess.get(_API, params={"index": "equities", "symbol": sym}, timeout=20)
                    resp.raise_for_status()
                    records = resp.json()
                    break
                except Exception:
                    time.sleep(1.5 * (attempt + 1))
                    try:
                        sess = _session()
                    except Exception:
                        pass
            if not isinstance(records, list):
                summary["failed"] += 1
                failed_in_row += 1
                if failed_in_row >= 15:  # NSE is refusing us (blocked address): stop instead of hammering it
                    break
                continue
            failed_in_row = 0
            rows = parse(records)
            pending.append((sym, rows))
            summary["updated" if rows else "no_data"] += 1
            if len(pending) >= BATCH:
                flush()
            time.sleep(pause)
    finally:
        flush()  # whatever was fetched is kept even if the run is cut short
    return summary


def figures(rows: list[tuple]) -> dict:
    """{'Public Holding', 'Public Δ 1Q', 'Public Δ 1Y'} from one stock's [(quarter, promoter, public)] rows; NaN for a stock with no promoter or too few quarters."""
    out = {c: np.nan for c in COLUMNS}
    if not rows:
        return out
    q = pd.Series({pd.Timestamp(r[0]): (r[1], r[2]) for r in rows}).sort_index()
    latest = q.index[-1]
    promoter, public = q.iloc[-1]
    if not promoter or promoter <= 0:
        return out
    out["Public Holding"] = public
    for col, months in (("Public Δ 1Q", 3), ("Public Δ 1Y", 12)):
        target = latest - pd.DateOffset(months=months)
        near = [t for t in q.index if abs((t - target).days) <= 10]
        if near:
            out[col] = public - q[near[-1]][1]
    return out


def load(symbols: list[str]) -> pd.DataFrame:
    """DataFrame indexed by symbol with the COLUMNS (empty if nothing is stored yet)."""
    uniq = sorted({s for s in symbols if isinstance(s, str) and s})
    if not uniq:
        return pd.DataFrame(columns=COLUMNS)
    conn = price_store._connect()
    try:
        _tables(conn)
        rows = conn.rows(f"SELECT symbol, quarter, promoter, public FROM stock_holding WHERE symbol IN ({','.join('?' * len(uniq))}) ORDER BY symbol, quarter", uniq)
    finally:
        conn.close()
    per = {}
    for s, q, pro, pub in rows:
        if pro is not None and pub is not None:
            per.setdefault(s, []).append((q, pro, pub))
    return pd.DataFrame({s: figures(r) for s, r in per.items()}).T.reindex(columns=COLUMNS) if per else pd.DataFrame(columns=COLUMNS)
