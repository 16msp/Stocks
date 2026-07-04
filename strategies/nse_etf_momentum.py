"""
NSE (India) ETF weekly momentum strategy.

Core logic only - no printing, no argparse, no sys.exit - so it can be driven
both by the CLI (nse_etf_tracker.py) and by the Streamlit UI (app.py / pages/).

Data flow:
  fetch()   -> pulls the live NSE ETF list + daily close/volume history from
               Yahoo Finance and stores it in a local SQLite DB. Incremental:
               only fetches days not already stored.
  analyze() -> reads the stored history, buckets it into ISO calendar weeks,
               and ranks ETFs by volume trend + price trend.
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable

import pandas as pd
import requests
import yfinance as yf

NSE_HOME = "https://www.nseindia.com/market-data/exchange-traded-funds-etf"
NSE_ETF_API = "https://www.nseindia.com/api/etf"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": NSE_HOME,
}

DB_PATH = Path(__file__).resolve().parent.parent / "nse_etf_data.db"
CHUNK_SIZE = 50
BACKFILL_DAYS = 45  # first run: enough calendar days for ~6 completed weeks
BAD_TICK_THRESHOLD = 0.40  # single-day close move bigger than this = suspected data glitch

Progress = Callable[[str], None]


def _noop(_msg: str) -> None:
    pass


# --------------------------------------------------------------------------
# NSE live ETF list
# --------------------------------------------------------------------------

def fetch_nse_etf_list() -> pd.DataFrame:
    session = requests.Session()
    session.headers.update(HEADERS)
    session.get(NSE_HOME, timeout=15)  # primes cookies NSE requires
    resp = session.get(NSE_ETF_API, timeout=15)
    resp.raise_for_status()
    rows = resp.json()["data"]

    df = pd.DataFrame(rows)[["symbol", "assets"]]
    return df.rename(columns={"assets": "category"})


# --------------------------------------------------------------------------
# SQLite storage
# --------------------------------------------------------------------------

def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS daily_prices (
            date TEXT NOT NULL,
            symbol TEXT NOT NULL,
            close REAL,
            volume INTEGER,
            PRIMARY KEY (date, symbol)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS etf_meta (
            symbol TEXT PRIMARY KEY,
            category TEXT
        )
        """
    )
    conn.commit()
    return conn


def get_db_status() -> dict:
    """Snapshot of what's stored locally, for display in the UI."""
    if not DB_PATH.exists():
        return {"exists": False}
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT MIN(date), MAX(date), COUNT(DISTINCT symbol), COUNT(DISTINCT date) FROM daily_prices"
        ).fetchone()
        symbol_count = conn.execute("SELECT COUNT(*) FROM etf_meta").fetchone()[0]
    finally:
        conn.close()
    first_date, last_date, priced_symbols, day_count = row
    return {
        "exists": first_date is not None,
        "first_date": first_date,
        "last_date": last_date,
        "priced_symbols": priced_symbols or 0,
        "day_count": day_count or 0,
        "known_etfs": symbol_count or 0,
    }


def _get_last_date(conn: sqlite3.Connection) -> date | None:
    row = conn.execute("SELECT MAX(date) FROM daily_prices").fetchone()
    if row and row[0]:
        return datetime.strptime(row[0], "%Y-%m-%d").date()
    return None


def _upsert_meta(conn: sqlite3.Connection, meta_df: pd.DataFrame) -> None:
    conn.executemany(
        "INSERT OR REPLACE INTO etf_meta (symbol, category) VALUES (?, ?)",
        meta_df[["symbol", "category"]].itertuples(index=False, name=None),
    )
    conn.commit()


def _upsert_prices(conn: sqlite3.Connection, prices_df: pd.DataFrame) -> int:
    rows = list(prices_df[["date", "symbol", "close", "volume"]].itertuples(index=False, name=None))
    conn.executemany(
        "INSERT OR REPLACE INTO daily_prices (date, symbol, close, volume) VALUES (?, ?, ?, ?)",
        rows,
    )
    conn.commit()
    return len(rows)


# --------------------------------------------------------------------------
# Yahoo Finance history fetch
# --------------------------------------------------------------------------

def _fetch_history(symbols: list[str], start: date, end: date) -> pd.DataFrame:
    """Return long-format DataFrame: date, symbol, close, volume."""
    yahoo_symbols = [f"{s}.NS" for s in symbols]
    start_str = start.strftime("%Y-%m-%d")
    end_str = (end + timedelta(days=1)).strftime("%Y-%m-%d")  # yfinance end is exclusive

    frames = []
    for i in range(0, len(yahoo_symbols), CHUNK_SIZE):
        chunk = yahoo_symbols[i : i + CHUNK_SIZE]
        data = None
        for _attempt in range(3):
            try:
                data = yf.download(
                    chunk,
                    start=start_str,
                    end=end_str,
                    interval="1d",
                    group_by="ticker",
                    threads=True,
                    progress=False,
                    auto_adjust=False,
                )
                break
            except Exception:
                time.sleep(2)
        if data is None or data.empty:
            continue

        for ysym in chunk:
            base = ysym[:-3]  # strip ".NS"
            try:
                sub = data["Close"] if len(chunk) == 1 else data[ysym]
                sub = sub[["Close", "Volume"]].dropna()
            except (KeyError, TypeError):
                continue
            if sub.empty:
                continue
            sub = sub.reset_index()
            sub["symbol"] = base
            sub["date"] = sub["Date"].dt.strftime("%Y-%m-%d")
            sub = sub.rename(columns={"Close": "close", "Volume": "volume"})
            frames.append(sub[["date", "symbol", "close", "volume"]])

    if not frames:
        return pd.DataFrame(columns=["date", "symbol", "close", "volume"])
    return pd.concat(frames, ignore_index=True)


# --------------------------------------------------------------------------
# fetch()
# --------------------------------------------------------------------------

@dataclass
class FetchResult:
    fetched: bool
    symbols_found: int = 0
    rows_stored: int = 0
    days_stored: int = 0
    symbols_stored: int = 0
    start: date | None = None
    end: date | None = None
    message: str = ""


def fetch(progress: Progress = _noop) -> FetchResult:
    conn = _connect()
    try:
        progress("Fetching live NSE ETF list...")
        etf_list = fetch_nse_etf_list()
        progress(f"  {len(etf_list)} ETFs found on NSE.")
        _upsert_meta(conn, etf_list)

        last_date = _get_last_date(conn)
        today = date.today()
        if last_date is None:
            start = today - timedelta(days=BACKFILL_DAYS)
            progress(f"No local history yet - backfilling from {start} to {today}.")
        else:
            start = last_date - timedelta(days=2)  # small overlap
            if start > today:
                progress("Local data already up to date.")
                return FetchResult(fetched=False, symbols_found=len(etf_list), message="Already up to date.")
            progress(f"Fetching new sessions from {start} to {today}...")

        prices = _fetch_history(etf_list["symbol"].tolist(), start, today)
        if prices.empty:
            msg = "No price data returned - NSE may be closed or Yahoo Finance is unreachable."
            progress(msg)
            return FetchResult(fetched=False, symbols_found=len(etf_list), message=msg)

        n_rows = _upsert_prices(conn, prices)
        n_days = prices["date"].nunique()
        n_symbols = prices["symbol"].nunique()
        msg = f"Stored {n_rows} rows ({n_symbols} ETFs x {n_days} trading day(s))."
        progress(msg)
        return FetchResult(
            fetched=True,
            symbols_found=len(etf_list),
            rows_stored=n_rows,
            days_stored=n_days,
            symbols_stored=n_symbols,
            start=start,
            end=today,
            message=msg,
        )
    finally:
        conn.close()


# --------------------------------------------------------------------------
# shared helpers (also used by other strategies, e.g. sector_reversal)
# --------------------------------------------------------------------------

def read_daily_prices() -> pd.DataFrame:
    """All stored daily close/volume rows, across every tracked ETF."""
    if not DB_PATH.exists():
        return pd.DataFrame(columns=["date", "symbol", "close", "volume"])
    conn = sqlite3.connect(DB_PATH)
    try:
        return pd.read_sql_query("SELECT * FROM daily_prices", conn, parse_dates=["date"])
    finally:
        conn.close()


def read_meta() -> pd.DataFrame:
    if not DB_PATH.exists():
        return pd.DataFrame(columns=["symbol", "category"])
    conn = sqlite3.connect(DB_PATH)
    try:
        return pd.read_sql_query("SELECT * FROM etf_meta", conn)
    finally:
        conn.close()


def drop_bad_ticks(prices: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Drop suspected bad ticks: a single-session close move bigger than
    BAD_TICK_THRESHOLD is essentially never real for an ETF and usually
    means a bad Yahoo Finance print (e.g. a misplaced decimal).

    Returns (cleaned_prices, dropped_rows).
    """
    prices = prices.sort_values(["symbol", "date"])
    day_change = prices.groupby("symbol")["close"].pct_change()
    bad_ticks = prices[day_change.abs() > BAD_TICK_THRESHOLD].copy()
    if not bad_ticks.empty:
        prices = prices.drop(bad_ticks.index)
    return prices, bad_ticks


def add_week_key(prices: pd.DataFrame) -> pd.DataFrame:
    prices = prices.copy()
    prices["iso_year"] = prices["date"].dt.isocalendar().year
    prices["iso_week"] = prices["date"].dt.isocalendar().week
    prices["week_key"] = prices["iso_year"].astype(str) + "-W" + prices["iso_week"].astype(str).str.zfill(2)
    return prices


# --------------------------------------------------------------------------
# analyze()
# --------------------------------------------------------------------------

@dataclass
class AnalyzeResult:
    ok: bool
    message: str = ""
    prev_week: str | None = None
    this_week: str | None = None
    bad_ticks: pd.DataFrame = field(default_factory=pd.DataFrame)
    full: pd.DataFrame = field(default_factory=pd.DataFrame)  # everything, incl. illiquid
    bullish: pd.DataFrame = field(default_factory=pd.DataFrame)
    caution: pd.DataFrame = field(default_factory=pd.DataFrame)
    fading: pd.DataFrame = field(default_factory=pd.DataFrame)
    quiet: pd.DataFrame = field(default_factory=pd.DataFrame)


def _signal(row: pd.Series) -> str:
    if row["volume_change_pct"] > 0 and row["price_change_pct"] > 0:
        return "BULLISH"
    if row["volume_change_pct"] > 0 and row["price_change_pct"] < 0:
        return "CAUTION"
    if row["volume_change_pct"] < 0 and row["price_change_pct"] > 0:
        return "FADING"
    return "QUIET"


def analyze(weeks: int = 2, top: int = 15, min_volume: int = 5000) -> AnalyzeResult:
    prices = read_daily_prices()
    meta = read_meta()

    if prices.empty:
        return AnalyzeResult(ok=False, message="No stored data yet. Run fetch first.")

    prices, bad_ticks = drop_bad_ticks(prices)
    prices = add_week_key(prices)
    prices = prices.sort_values("date")
    weekly = (
        prices.groupby(["symbol", "week_key"])
        .agg(week_volume=("volume", "sum"), week_close=("close", "last"), last_date=("date", "max"))
        .reset_index()
    )

    week_order = (
        weekly[["week_key", "last_date"]]
        .drop_duplicates("week_key")
        .sort_values("last_date")["week_key"]
        .tolist()
    )
    if len(week_order) < 2:
        return AnalyzeResult(
            ok=False,
            message=(
                "Only one week of data stored so far - need at least 2 weekly fetches "
                "(or a longer backfill) to compute a trend. Run fetch again next week."
            ),
            bad_ticks=bad_ticks,
        )

    n_weeks = min(weeks, len(week_order))
    recent_weeks = week_order[-n_weeks:]
    this_week, prev_week = week_order[-1], week_order[-2]

    pivot_vol = weekly[weekly["week_key"].isin(recent_weeks)].pivot(index="symbol", columns="week_key", values="week_volume")
    pivot_close = weekly[weekly["week_key"].isin(recent_weeks)].pivot(index="symbol", columns="week_key", values="week_close")

    result = pd.DataFrame(index=pivot_vol.index)
    result["this_week_volume"] = pivot_vol[this_week]
    result["prev_week_volume"] = pivot_vol[prev_week]
    result["this_week_close"] = pivot_close[this_week]
    result["prev_week_close"] = pivot_close[prev_week]
    result = result.dropna(subset=["this_week_volume", "prev_week_volume", "this_week_close", "prev_week_close"])

    result["volume_change_pct"] = (result["this_week_volume"] - result["prev_week_volume"]) / result["prev_week_volume"] * 100
    result["price_change_pct"] = (result["this_week_close"] - result["prev_week_close"]) / result["prev_week_close"] * 100
    result = result.reset_index().merge(meta, on="symbol", how="left")

    result["signal"] = result.apply(_signal, axis=1)
    result = result.sort_values("volume_change_pct", ascending=False).reset_index(drop=True)
    result["liquid"] = result["prev_week_volume"] >= min_volume

    liquid_result = result[result["liquid"]]
    bullish = liquid_result[liquid_result["signal"] == "BULLISH"].head(top).reset_index(drop=True)
    caution = liquid_result[liquid_result["signal"] == "CAUTION"].head(top).reset_index(drop=True)
    fading = liquid_result[liquid_result["signal"] == "FADING"].head(top).reset_index(drop=True)
    quiet = liquid_result[liquid_result["signal"] == "QUIET"].head(top).reset_index(drop=True)

    return AnalyzeResult(
        ok=True,
        message=f"Weeks compared: {prev_week} (prev) -> {this_week} (this).",
        prev_week=prev_week,
        this_week=this_week,
        bad_ticks=bad_ticks,
        full=result,
        bullish=bullish,
        caution=caution,
        fading=fading,
        quiet=quiet,
    )
