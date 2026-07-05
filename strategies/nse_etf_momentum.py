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

import re
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable

import numpy as np
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
            high REAL,
            low REAL,
            open REAL,
            PRIMARY KEY (date, symbol)
        )
        """
    )
    existing_cols = {row[1] for row in conn.execute("PRAGMA table_info(daily_prices)")}
    if "high" not in existing_cols:
        conn.execute("ALTER TABLE daily_prices ADD COLUMN high REAL")
    if "low" not in existing_cols:
        conn.execute("ALTER TABLE daily_prices ADD COLUMN low REAL")
    if "open" not in existing_cols:
        conn.execute("ALTER TABLE daily_prices ADD COLUMN open REAL")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS etf_meta (
            symbol TEXT PRIMARY KEY,
            category TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS backfill_state (
            symbol TEXT PRIMARY KEY,
            deep_backfilled_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS paper_trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sector TEXT NOT NULL,
            entry_date TEXT NOT NULL,
            entry_index REAL NOT NULL,
            entry_threshold REAL NOT NULL,
            target_pct REAL NOT NULL,
            horizon_days INTEGER NOT NULL,
            status TEXT NOT NULL,
            exit_date TEXT,
            exit_index REAL,
            exit_reason TEXT,
            return_pct REAL
        )
        """
    )
    conn.commit()
    return conn


def get_connection() -> sqlite3.Connection:
    """Public entry point for other strategy modules that need direct DB access
    (e.g. the paper-trades table used by sector_reversal_live)."""
    return _connect()


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
    rows = list(prices_df[["date", "symbol", "close", "volume", "high", "low", "open"]].itertuples(index=False, name=None))
    conn.executemany(
        "INSERT OR REPLACE INTO daily_prices (date, symbol, close, volume, high, low, open) VALUES (?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    conn.commit()
    return len(rows)


def _get_first_dates(conn: sqlite3.Connection, symbols: list[str]) -> dict:
    """Earliest stored date per symbol, for deciding which symbols still need a deep backfill."""
    if not symbols:
        return {}
    placeholders = ",".join("?" * len(symbols))
    rows = conn.execute(
        f"SELECT symbol, MIN(date) FROM daily_prices WHERE symbol IN ({placeholders}) GROUP BY symbol",
        symbols,
    ).fetchall()
    return {sym: datetime.strptime(d, "%Y-%m-%d").date() for sym, d in rows}


# --------------------------------------------------------------------------
# Yahoo Finance history fetch
# --------------------------------------------------------------------------

def _fetch_history(symbols: list[str], start: date, end: date) -> pd.DataFrame:
    """Return long-format DataFrame: date, symbol, close, high, low, open, volume."""
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
                sub = data[ysym]
                sub = sub[["Close", "High", "Low", "Open", "Volume"]].dropna(subset=["Close", "Volume"])
            except (KeyError, TypeError):
                continue
            if sub.empty:
                continue
            sub = sub.reset_index()
            sub["symbol"] = base
            sub["date"] = sub["Date"].dt.strftime("%Y-%m-%d")
            sub = sub.rename(columns={"Close": "close", "High": "high", "Low": "low", "Open": "open", "Volume": "volume"})
            frames.append(sub[["date", "symbol", "close", "high", "low", "open", "volume"]])

    if not frames:
        return pd.DataFrame(columns=["date", "symbol", "close", "high", "low", "open", "volume"])
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
# deep historical backfill (one-time, for backtesting - separate from the
# regular incremental fetch() used by the weekly refresh button)
# --------------------------------------------------------------------------

DEEP_BACKFILL_TARGET_START = date(2005, 1, 1)  # before any NSE ETF existed


@dataclass
class BackfillResult:
    ran: bool
    symbols_checked: int = 0
    symbols_skipped: int = 0
    symbols_fetched: int = 0
    rows_stored: int = 0
    message: str = ""


def backfill_history(symbols: list[str], force: bool = False, progress: Progress = _noop) -> BackfillResult:
    """One-time deep backfill of full available history for the given symbols
    (e.g. sector ETFs), so there's enough data to backtest across market
    cycles. Idempotent and cheap to re-run: once a symbol has been deep-
    backfilled it's recorded in backfill_state, and subsequent calls skip it
    (unless force=True) - so this never re-downloads years of data it already
    has. Safe to call alongside the regular fetch() used for weekly refreshes
    - they write to the same daily_prices table.
    """
    conn = _connect()
    try:
        if force:
            to_fetch = symbols
            skipped = 0
        else:
            done = {row[0] for row in conn.execute("SELECT symbol FROM backfill_state")}
            to_fetch = [s for s in symbols if s not in done]
            skipped = len(symbols) - len(to_fetch)

        if not to_fetch:
            progress(f"All {len(symbols)} symbols already deep-backfilled - nothing to do.")
            return BackfillResult(ran=False, symbols_checked=len(symbols), symbols_skipped=skipped, message="Already up to date.")

        progress(f"{skipped} symbol(s) already deep-backfilled, skipping. Fetching full history for {len(to_fetch)} symbol(s)...")
        prices = _fetch_history(to_fetch, DEEP_BACKFILL_TARGET_START, date.today())
        if prices.empty:
            msg = "No historical data returned."
            progress(msg)
            return BackfillResult(ran=True, symbols_checked=len(symbols), symbols_skipped=skipped, message=msg)

        n_rows = _upsert_prices(conn, prices)
        n_symbols = prices["symbol"].nunique()
        now = datetime.now().isoformat(timespec="seconds")
        conn.executemany(
            "INSERT OR REPLACE INTO backfill_state (symbol, deep_backfilled_at) VALUES (?, ?)",
            [(s, now) for s in to_fetch],
        )
        conn.commit()
        msg = f"Deep-backfilled {n_symbols} symbol(s), {n_rows} rows ({prices['date'].min()} to {prices['date'].max()})."
        progress(msg)
        return BackfillResult(
            ran=True,
            symbols_checked=len(symbols),
            symbols_skipped=skipped,
            symbols_fetched=n_symbols,
            rows_stored=n_rows,
            message=msg,
        )
    finally:
        conn.close()


def get_history_depth(symbols: list[str]) -> pd.DataFrame:
    """Per-symbol earliest stored date + row count, so the UI can show how much backtestable history exists."""
    conn = _connect()
    try:
        first_dates = _get_first_dates(conn, symbols)
        rows = conn.execute(
            f"SELECT symbol, COUNT(*) FROM daily_prices WHERE symbol IN ({','.join('?' * len(symbols))}) GROUP BY symbol",
            symbols,
        ).fetchall() if symbols else []
        counts = dict(rows)
    finally:
        conn.close()
    out = pd.DataFrame({"symbol": symbols})
    out["first_date"] = out["symbol"].map(first_dates)
    out["days_stored"] = out["symbol"].map(counts).fillna(0).astype(int)
    today = date.today()
    out["years_of_history"] = out["first_date"].apply(lambda d: (today - d).days / 365.25 if pd.notna(d) else 0.0)
    return out


# --------------------------------------------------------------------------
# shared helpers (also used by other strategies, e.g. sector_reversal)
# --------------------------------------------------------------------------

def read_daily_prices() -> pd.DataFrame:
    """All stored daily close/high/low/open/volume rows, across every tracked ETF."""
    if not DB_PATH.exists():
        return pd.DataFrame(columns=["date", "symbol", "close", "high", "low", "open", "volume"])
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


_CATEGORY_SUFFIXES = ("totalreturnindex", "totalreturn", "tri", "index", "etf")

# AMC/provider brand names that show up as a literal prefix in some categories
# (e.g. "HDFC NIFTY IT ETF", "Mirae Asset Nifty IT ETF") instead of a plain
# index name (e.g. "Nifty IT TRI") - without stripping these, the same
# underlying index gets a different group key per AMC and dedup silently
# fails for every ETF whose category is written in "<Brand> <Index> ETF"
# form. Longest-first so e.g. "miraeassetmutualfund" is tried before
# "miraeasset".
_PROVIDER_PREFIXES = tuple(sorted(
    [
        "adityabirlasunlife", "axis", "bajajfinserv", "barodabnpparibas", "dsp",
        "edelweiss", "hdfc", "iciciprudential", "kotak", "licmf", "lic",
        "miraeassetmutualfund", "miraeasset", "motilaloswal", "sbi", "shriram",
        "uti", "zerodha",
    ],
    key=len, reverse=True,
))


def _normalize_category(category: str) -> str:
    """Collapse near-duplicate NSE category strings (e.g. 'Nifty 50', 'Nifty 50
    Index', 'Nifty 50 Index - TRI', 'Nifty 50 TRI', 'HDFC NIFTY 50 ETF') down
    to one group key, so ETFs tracking the same underlying index from
    different AMCs group together regardless of whether the category is a
    plain index name or a branded "<AMC> <Index> ETF" product title.
    Deliberately keeps real product differences distinct (e.g. 'Nifty 50
    Equal Weight' vs 'Nifty 50') since those aren't duplicates.
    """
    s = re.sub(r"[^a-z0-9]", "", category.lower())
    for prefix in _PROVIDER_PREFIXES:
        if s.startswith(prefix) and len(s) > len(prefix):
            s = s[len(prefix):]
            break
    changed = True
    while changed:
        changed = False
        for suf in _CATEGORY_SUFFIXES:
            if s.endswith(suf) and len(s) > len(suf):
                s = s[: -len(suf)]
                changed = True
    return s


def get_representative_symbols(lookback_days: int = 90) -> pd.DataFrame:
    """One ETF per underlying-index group (see _normalize_category), keeping
    the highest-average-volume symbol in each group as the "representative" -
    tracking every near-duplicate ETF from every AMC adds clutter, not
    diversification, for strategies that trade individual instruments.

    Returns all symbols with a `group_key` and `is_representative` flag (not
    just the winners), so callers/UIs can show what got folded into what.
    """
    meta = read_meta()
    prices = read_daily_prices()
    if meta.empty:
        return pd.DataFrame(columns=["symbol", "category", "group_key", "avg_volume", "is_representative"])

    if prices.empty:
        avg_vol = pd.Series(dtype=float, name="avg_volume")
    else:
        cutoff = prices["date"].max() - pd.Timedelta(days=lookback_days)
        recent = prices[prices["date"] >= cutoff]
        avg_vol = recent.groupby("symbol")["volume"].mean().rename("avg_volume")

    df = meta.merge(avg_vol, on="symbol", how="left")
    df["avg_volume"] = df["avg_volume"].fillna(0)
    df["group_key"] = df["category"].apply(_normalize_category)
    df = df.sort_values("avg_volume", ascending=False)
    df["is_representative"] = ~df.duplicated("group_key", keep="first")
    return df.sort_values(["group_key", "avg_volume"], ascending=[True, False]).reset_index(drop=True)


def get_representative_symbol_list(lookback_days: int = 90) -> list[str]:
    df = get_representative_symbols(lookback_days)
    return df[df["is_representative"]]["symbol"].tolist()


INTRADAY_RANGE_THRESHOLD = 0.20  # same-day high/low more than this far from close = suspected bad print


def drop_bad_ticks(prices: pd.DataFrame, max_passes: int = 5) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Drop or repair suspected bad ticks - Yahoo Finance occasionally prints a
    wrong value for one field on one day (e.g. a misplaced decimal), and a
    single-day/single-field check can miss it if a neighboring day is also bad.

    Two independent checks, both iterated to convergence:
      1. Close: a day-over-day close move bigger than BAD_TICK_THRESHOLD is
         essentially never real for an ETF. Whole row dropped (close/high/low/
         volume all suspect once close itself is wrong).
      2. High/Low: a same-day high/low more than INTRADAY_RANGE_THRESHOLD away
         from that day's own close implies an intraday round-trip that's not
         realistic for a diversified ETF. Only that one field is nulled out
         (close/volume for the day are usually still fine), so this doesn't
         lose otherwise-valid data - it just won't be used for touch-based
         entry/exit checks on that specific day.

    Returns (cleaned_prices, all_flagged_rows) - flagged rows include both
    fully-dropped close anomalies and rows with a nulled high/low.
    """
    prices = prices.sort_values(["symbol", "date"]).copy()
    all_bad = []
    for _ in range(max_passes):
        day_change = prices.groupby("symbol")["close"].pct_change()
        bad_ticks = prices[day_change.abs() > BAD_TICK_THRESHOLD]
        if bad_ticks.empty:
            break
        all_bad.append(bad_ticks.assign(bad_reason="close_jump"))
        prices = prices.drop(bad_ticks.index)

    bad_low = prices[prices["low"] < prices["close"] * (1 - INTRADAY_RANGE_THRESHOLD)]
    if not bad_low.empty:
        all_bad.append(bad_low.assign(bad_reason="bad_low"))
        prices.loc[bad_low.index, "low"] = np.nan

    bad_high = prices[prices["high"] > prices["close"] * (1 + INTRADAY_RANGE_THRESHOLD)]
    if not bad_high.empty:
        all_bad.append(bad_high.assign(bad_reason="bad_high"))
        prices.loc[bad_high.index, "high"] = np.nan

    # open=0 shows up for many early (2008-2010) sessions on some symbols -
    # a real Yahoo Finance data gap, not an actual free trade. Also catch an
    # open implausibly far from that day's close, same logic as high/low.
    if "open" in prices.columns:
        bad_open = prices[
            (prices["open"] <= 0)
            | (prices["open"] < prices["close"] * (1 - INTRADAY_RANGE_THRESHOLD))
            | (prices["open"] > prices["close"] * (1 + INTRADAY_RANGE_THRESHOLD))
        ]
        if not bad_open.empty:
            all_bad.append(bad_open.assign(bad_reason="bad_open"))
            prices.loc[bad_open.index, "open"] = np.nan

    bad_ticks = pd.concat(all_bad) if all_bad else prices.iloc[0:0].assign(bad_reason=[])
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
