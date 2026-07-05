"""
ATH-drop averaging strategy: buy after a deep drawdown, average down twice more
on further drops, exit each tranche independently once it clears a return bar.

Rule (finalized after backtesting - see conversation/Backtest page):
  Entry:  buy tranche 1 when price first touches -20% below its running
          all-time high (intraday low vs. running intraday-high ATH).
  Averaging: buy tranche 2 on a further -10% touch from tranche 1's entry
          price; tranche 3 on a further -10% from tranche 2's entry. Max 3
          tranches per cycle.
  Exit (each tranche tracked independently):
    - held < 1 year:  sell at +25% absolute gain
    - held >= 1 year: sell once the position clears 15% CAGR since entry
          (the required price rises every day held, compounding at 15%/yr)
  A cycle resets (free to open a fresh tranche 1) once every tranche bought
  in it has resolved (won or timed out) - it does not wait for tranches that
  were never triggered.

This trades real, individual tradeable ETFs (not a synthetic sector
composite), using the same daily close/high/low history nse_etf_momentum
stores. Backtesting needs deep (multi-year) history per symbol - see
nse_etf_momentum.backfill_history(). Live tracking works for any symbol
regardless of history depth.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime

import numpy as np
import pandas as pd

from strategies import nse_etf_momentum as base

TRADE_SIZE = 50000.0
ATH_DROP_TRIGGER = 0.20
AVG_DROP_TRIGGER = 0.10
MAX_TRANCHES = 3
SHORT_TERM_DAYS = 365
SHORT_TERM_GAIN = 0.25
LONG_TERM_CAGR = 0.15
MIN_YEARS_FOR_BACKTEST = 1.0  # need at least ~1yr of history to be worth simulating

EXCLUDED_CATEGORY_KEYWORDS = ("gold", "silver")  # commodity ETFs - not equity dip-buys
MIN_AVG_VOLUME = 10000  # below this, an ETF is too thin to be worth tracking here


def get_universe(min_avg_volume: int = MIN_AVG_VOLUME, exclude_keywords: tuple = EXCLUDED_CATEGORY_KEYWORDS) -> pd.DataFrame:
    """The deduplicated, filtered ETF universe this strategy trades: one ETF
    per underlying index (highest volume), gold/silver excluded, and thin
    ETFs below min_avg_volume dropped. Returns symbol/category/avg_volume for
    every considered ETF (not just the survivors) so the UI can show why an
    ETF was excluded.
    """
    df = base.get_representative_symbols()
    if df.empty:
        return df
    df = df[df["is_representative"]].copy()
    # Check both the NSE category text and the symbol itself - some gold/silver
    # ETFs (e.g. GROWWGOLD) are filed under a generic category like "Commodity"
    # with no "gold"/"silver" in the text, but it's plain in the ticker.
    haystack = (df["category"].str.lower() + " " + df["symbol"].str.lower())
    keyword_mask = haystack.apply(lambda s: any(k in s for k in exclude_keywords))
    df["excluded_commodity"] = keyword_mask
    df["excluded_low_volume"] = df["avg_volume"] < min_avg_volume
    df["included"] = ~(df["excluded_commodity"] | df["excluded_low_volume"])
    return df.reset_index(drop=True)


def get_universe_symbol_list(min_avg_volume: int = MIN_AVG_VOLUME) -> list[str]:
    df = get_universe(min_avg_volume)
    return df[df["included"]]["symbol"].tolist() if not df.empty else []


# --------------------------------------------------------------------------
# Backtest
# --------------------------------------------------------------------------

def _simulate_symbol(df: pd.DataFrame) -> list[dict]:
    """df: single symbol's daily close/high/low/open, sorted by date. Returns tranche records.

    ATH is tracked off closing price alone - the standard, official reference
    (matches what platforms like TradingView report) and immune to a bad
    print in a single other field. Entry/averaging/exit "touch" checks use
    day_high/day_low = max/min(open, close) rather than the raw intraday
    high/low - a fleeting intraday tick isn't reliably tradable, but the open
    and close are real, settled prices you could actually have transacted at.
    """
    dates = df["date"].to_numpy()
    close = df["close"].to_numpy()
    open_ = df["open"].to_numpy()
    # np.maximum/minimum propagate NaN rather than skipping it - fall back to
    # close alone on days with a missing/invalid open (dropped by drop_bad_ticks).
    open_valid = ~np.isnan(open_)
    day_high = np.where(open_valid, np.maximum(open_, close), close)
    day_low = np.where(open_valid, np.minimum(open_, close), close)
    n = len(df)
    if n == 0:
        return []
    ath = np.maximum.accumulate(np.nan_to_num(close, nan=-np.inf))

    trades: list[dict] = []
    cycle: list[dict] = []
    next_tranche = 1

    for t in range(n):
        if np.isnan(day_low[t]) or np.isnan(day_high[t]):
            continue
        today_ath = ath[t]

        if next_tranche == 1:
            trigger = today_ath * (1 - ATH_DROP_TRIGGER)
            if day_low[t] <= trigger:
                cycle.append({"tranche": 1, "entry_date": dates[t], "entry_price": trigger, "status": "OPEN", "min_low": day_low[t]})
                next_tranche = 2
        elif next_tranche in (2, 3):
            last_price = cycle[-1]["entry_price"]
            trigger = last_price * (1 - AVG_DROP_TRIGGER)
            if day_low[t] <= trigger:
                cycle.append({"tranche": next_tranche, "entry_date": dates[t], "entry_price": trigger, "status": "OPEN", "min_low": day_low[t]})
                next_tranche = next_tranche + 1 if next_tranche < MAX_TRANCHES else None

        for tr in cycle:
            if tr["status"] != "OPEN":
                continue
            tr["min_low"] = min(tr["min_low"], day_low[t])
            days_held = int((dates[t] - tr["entry_date"]) / np.timedelta64(1, "D"))
            target_return = SHORT_TERM_GAIN if days_held < SHORT_TERM_DAYS else (1 + LONG_TERM_CAGR) ** (days_held / 365.0) - 1
            target_price = tr["entry_price"] * (1 + target_return)
            if day_high[t] >= target_price:
                max_dd = (tr["min_low"] / tr["entry_price"] - 1) * 100
                tr.update(status="WIN", exit_date=dates[t], days_held=days_held, return_pct=target_return * 100, max_drawdown_pct=max_dd)

        if cycle and all(tr["status"] != "OPEN" for tr in cycle):
            trades.extend(cycle)
            cycle = []
            next_tranche = 1

    for tr in cycle:
        if tr["status"] == "OPEN":
            days_held = int((dates[-1] - tr["entry_date"]) / np.timedelta64(1, "D"))
            max_dd = (tr["min_low"] / tr["entry_price"] - 1) * 100
            tr.update(status="PENDING", days_held=days_held, return_pct=(close[-1] / tr["entry_price"] - 1) * 100, max_drawdown_pct=max_dd)
        trades.append(tr)

    for tr in trades:
        tr.pop("min_low", None)

    return trades


def compute_xirr(cash_flows: list[tuple[float, date]]) -> float | None:
    """Solve for the annualized rate r where sum(amount / (1+r)^(years since t0)) = 0.
    The correct way to express "CAGR as a whole" for a strategy whose trades
    open and close on different dates with (here) equal-sized cash flows -
    plain total-return-to-CAGR math assumes one lump sum in and out, which
    doesn't fit a rolling set of overlapping trades. Returns None if it can't
    bracket a root (e.g. all cash flows the same sign).
    """
    if len(cash_flows) < 2:
        return None
    t0 = min(d for _, d in cash_flows)

    def npv(rate: float) -> float:
        return sum(amt / (1 + rate) ** ((d - t0).days / 365.0) for amt, d in cash_flows)

    lo, hi = -0.9999, 20.0
    f_lo, f_hi = npv(lo), npv(hi)
    if f_lo * f_hi > 0:
        return None
    for _ in range(200):
        mid = (lo + hi) / 2
        f_mid = npv(mid)
        if abs(f_mid) < 1e-6:
            break
        if f_lo * f_mid < 0:
            hi = mid
        else:
            lo, f_lo = mid, f_mid
    return mid * 100


@dataclass
class AthBacktestResult:
    ok: bool
    message: str = ""
    trades: pd.DataFrame = field(default_factory=pd.DataFrame)
    per_symbol: pd.DataFrame = field(default_factory=pd.DataFrame)
    excluded: dict = field(default_factory=dict)
    overall_cagr_pct: float | None = None
    total_realized_gain: float = 0.0
    total_deployed: float = 0.0


def run_backtest(symbols: list[str] | None = None, min_years: float = MIN_YEARS_FOR_BACKTEST) -> AthBacktestResult:
    prices = base.read_daily_prices()
    if prices.empty:
        return AthBacktestResult(ok=False, message="No stored data yet.")
    prices, _bad = base.drop_bad_ticks(prices)

    if symbols is None:
        symbols = get_universe_symbol_list()
    depth = base.get_history_depth(symbols).set_index("symbol")["years_of_history"].to_dict()

    all_trades = []
    excluded = {}
    for sym in symbols:
        years = depth.get(sym, 0.0)
        if years < min_years:
            excluded[sym] = f"only {years:.1f}y of history"
            continue
        df = prices[prices["symbol"] == sym].sort_values("date").reset_index(drop=True)
        recs = _simulate_symbol(df)
        for r in recs:
            r["symbol"] = sym
        all_trades.extend(recs)

    if not all_trades:
        return AthBacktestResult(ok=False, message="No symbols had enough history to backtest.", excluded=excluded)

    trades_df = pd.DataFrame(all_trades)
    trades_df["entry_date"] = pd.to_datetime(trades_df["entry_date"]).dt.date
    trades_df["exit_date"] = pd.to_datetime(trades_df["exit_date"]).dt.date if "exit_date" in trades_df else None
    trades_df["gain_rupees"] = TRADE_SIZE * trades_df["return_pct"] / 100

    meta = base.read_meta().rename(columns={"category": "description"})
    trades_df = trades_df.merge(meta, on="symbol", how="left")

    resolved = trades_df[trades_df["status"] == "WIN"]
    per_symbol = (
        trades_df.groupby("symbol")
        .agg(
            description=("description", "first"),
            tranches=("status", "count"),
            wins=("status", lambda s: (s == "WIN").sum()),
            pending=("status", lambda s: (s == "PENDING").sum()),
            realized_gain=("gain_rupees", lambda s: s[trades_df.loc[s.index, "status"] == "WIN"].sum()),
            avg_days_held=("days_held", lambda s: trades_df.loc[s.index][trades_df.loc[s.index, "status"] == "WIN"]["days_held"].mean()),
            worst_drawdown_pct=("max_drawdown_pct", "min"),
        )
        .reset_index()
    )
    per_symbol["win_rate_pct"] = per_symbol["wins"] / per_symbol["tranches"] * 100

    n_symbols = trades_df["symbol"].nunique()
    total_realized_gain = resolved["gain_rupees"].sum()
    total_deployed = len(trades_df) * TRADE_SIZE

    # XIRR across every tranche ever opened: -TRADE_SIZE at entry, +exit value at
    # exit (or mark-to-market "today" for still-open ones) - this is the correct
    # "CAGR as a whole" for a strategy with many overlapping trades of different
    # durations, since a simple total-return calc assumes one lump sum in/out.
    as_of = prices["date"].max().date()
    cash_flows = []
    for _, row in trades_df.iterrows():
        cash_flows.append((-TRADE_SIZE, row["entry_date"]))
        exit_date = row["exit_date"] if row["status"] == "WIN" else as_of
        cash_flows.append((TRADE_SIZE * (1 + row["return_pct"] / 100), exit_date))
    overall_cagr = compute_xirr(cash_flows)

    cagr_str = f"{overall_cagr:.1f}%" if overall_cagr is not None else "n/a"
    message = (
        f"{len(resolved)} resolved trades across {n_symbols} symbol(s) "
        f"({len(trades_df) - len(resolved)} still open/pending). "
        f"Total realized gain: Rs.{total_realized_gain:,.0f} on Rs.{total_deployed:,.0f} deployed. "
        f"Overall CAGR (XIRR, incl. mark-to-market on open positions): {cagr_str}."
    )
    return AthBacktestResult(
        ok=True, message=message, trades=trades_df, per_symbol=per_symbol, excluded=excluded,
        overall_cagr_pct=overall_cagr, total_realized_gain=total_realized_gain, total_deployed=total_deployed,
    )


# --------------------------------------------------------------------------
# Live paper trading
# --------------------------------------------------------------------------

def _init_ath_table(conn) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS ath_positions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            cycle_seq INTEGER NOT NULL,
            tranche INTEGER NOT NULL,
            entry_date TEXT NOT NULL,
            entry_price REAL NOT NULL,
            status TEXT NOT NULL,
            exit_date TEXT,
            exit_price REAL,
            return_pct REAL,
            min_low REAL,
            max_drawdown_pct REAL
        )
        """
    )
    existing_cols = {row[1] for row in conn.execute("PRAGMA table_info(ath_positions)")}
    for col in ("min_low", "max_drawdown_pct"):
        if col not in existing_cols:
            conn.execute(f"ALTER TABLE ath_positions ADD COLUMN {col} REAL")
    conn.commit()


WATCHLIST_MIN_DROP = 0.10  # show flat symbols already down at least this much from ATH, even if short of the entry trigger


@dataclass
class AthLiveResult:
    ok: bool
    message: str = ""
    newly_opened: pd.DataFrame = field(default_factory=pd.DataFrame)
    newly_closed: pd.DataFrame = field(default_factory=pd.DataFrame)
    open_positions: pd.DataFrame = field(default_factory=pd.DataFrame)
    meets_criteria: pd.DataFrame = field(default_factory=pd.DataFrame)
    watchlist: pd.DataFrame = field(default_factory=pd.DataFrame)


def get_open_positions() -> pd.DataFrame:
    conn = base.get_connection()
    try:
        _init_ath_table(conn)
        return pd.read_sql_query("SELECT * FROM ath_positions WHERE status='OPEN'", conn)
    finally:
        conn.close()


def get_trade_history() -> pd.DataFrame:
    conn = base.get_connection()
    try:
        _init_ath_table(conn)
        return pd.read_sql_query("SELECT * FROM ath_positions WHERE status != 'OPEN' ORDER BY exit_date DESC", conn)
    finally:
        conn.close()


def check_signals(symbols: list[str] | None = None) -> AthLiveResult:
    prices = base.read_daily_prices()
    if prices.empty:
        return AthLiveResult(ok=False, message="No stored data yet.")
    prices, _bad = base.drop_bad_ticks(prices)
    stored_symbols = set(prices["symbol"].unique())
    if symbols is None:
        conn = base.get_connection()
        _init_ath_table(conn)
        already_open = {row[0] for row in conn.execute("SELECT DISTINCT symbol FROM ath_positions WHERE status='OPEN'")}
        conn.close()
        # Always keep monitoring symbols with an existing open position, even if
        # they've since been folded out of the deduplicated/filtered universe -
        # a narrower universe going forward shouldn't orphan a trade in flight.
        symbols = sorted(set(get_universe_symbol_list()) | already_open)

    meta = base.read_meta().set_index("symbol")["category"].to_dict()
    conn = base.get_connection()
    _init_ath_table(conn)
    today = date.today()
    newly_opened, newly_closed, meets_criteria, watchlist = [], [], [], []

    try:
        for sym in symbols:
            if sym not in stored_symbols:
                continue
            df = prices[prices["symbol"] == sym].sort_values("date").reset_index(drop=True)
            if df.empty:
                continue
            latest_date = df["date"].iloc[-1]
            latest_close = df["close"].iloc[-1]
            latest_open = df["open"].iloc[-1]
            if pd.isna(latest_close):
                continue
            # Day high/low proxied by open/close, not the raw intraday tick -
            # a fleeting intraday print isn't reliably tradable. Falls back to
            # close alone on a missing/invalid open (dropped by drop_bad_ticks).
            if pd.isna(latest_open):
                latest_high = latest_low = latest_close
            else:
                latest_high = max(latest_open, latest_close)
                latest_low = min(latest_open, latest_close)
            # ATH tracked off closing price alone - see _simulate_symbol docstring.
            ath = df["close"].max()
            description = meta.get(sym, "")

            open_rows = pd.read_sql_query(
                "SELECT * FROM ath_positions WHERE symbol=? AND status='OPEN' ORDER BY tranche", conn, params=(sym,)
            )

            # --- check exits on any open tranche (update running max-drawdown first) ---
            for _, row in open_rows.iterrows():
                min_low = min(row["min_low"], latest_low) if pd.notna(row["min_low"]) else latest_low
                conn.execute("UPDATE ath_positions SET min_low=? WHERE id=?", (float(min_low), int(row["id"])))

                entry_date = datetime.strptime(row["entry_date"], "%Y-%m-%d").date()
                days_held = (today - entry_date).days
                target_return = SHORT_TERM_GAIN if days_held < SHORT_TERM_DAYS else (1 + LONG_TERM_CAGR) ** (days_held / 365.0) - 1
                target_price = row["entry_price"] * (1 + target_return)
                if latest_high >= target_price:
                    return_pct = target_return * 100
                    max_dd = (min_low / row["entry_price"] - 1) * 100
                    conn.execute(
                        "UPDATE ath_positions SET status='WIN', exit_date=?, exit_price=?, return_pct=?, max_drawdown_pct=? WHERE id=?",
                        (str(latest_date.date()), float(target_price), float(return_pct), float(max_dd), int(row["id"])),
                    )
                    newly_closed.append({**row.to_dict(), "return_pct": return_pct, "max_drawdown_pct": max_dd, "description": description})

            # --- check entry / averaging ---
            open_rows = pd.read_sql_query(
                "SELECT * FROM ath_positions WHERE symbol=? AND status='OPEN' ORDER BY tranche", conn, params=(sym,)
            )
            last_cycle_row = conn.execute(
                "SELECT MAX(cycle_seq) FROM ath_positions WHERE symbol=?", (sym,)
            ).fetchone()
            last_cycle_seq = last_cycle_row[0] or 0

            if open_rows.empty:
                trigger = ath * (1 - ATH_DROP_TRIGGER)
                if latest_low <= trigger:
                    conn.execute(
                        "INSERT INTO ath_positions (symbol, cycle_seq, tranche, entry_date, entry_price, status, min_low) VALUES (?, ?, 1, ?, ?, 'OPEN', ?)",
                        (sym, last_cycle_seq + 1, str(latest_date.date()), float(trigger), float(latest_low)),
                    )
                    rec = {"symbol": sym, "description": description, "tranche": 1, "entry_date": str(latest_date.date()),
                           "entry_price": trigger, "ath": ath, "pct_below_ath": (trigger / ath - 1) * 100}
                    newly_opened.append(rec)
                    meets_criteria.append(rec)
                else:
                    current_pct_below_ath = (latest_close / ath - 1) * 100
                    if current_pct_below_ath <= -WATCHLIST_MIN_DROP * 100:
                        watchlist.append({
                            "symbol": sym, "description": description, "current_price": latest_close,
                            "ath": ath, "pct_below_ath": current_pct_below_ath,
                            "pct_to_go": current_pct_below_ath - (-ATH_DROP_TRIGGER * 100),
                        })
            elif len(open_rows) < MAX_TRANCHES:
                last_tranche = open_rows.iloc[-1]
                last_entry_date = datetime.strptime(last_tranche["entry_date"], "%Y-%m-%d").date()
                trigger = last_tranche["entry_price"] * (1 - AVG_DROP_TRIGGER)
                # Only average on a genuinely later trading day than the last tranche's
                # entry - otherwise re-running check_signals() on the same day's data
                # (no new prices) could re-use that same day's low to justify a second
                # tranche it already justified for the first one.
                if latest_date.date() > last_entry_date and latest_low <= trigger:
                    next_tranche_no = int(last_tranche["tranche"]) + 1
                    conn.execute(
                        "INSERT INTO ath_positions (symbol, cycle_seq, tranche, entry_date, entry_price, status, min_low) VALUES (?, ?, ?, ?, ?, 'OPEN', ?)",
                        (sym, int(last_tranche["cycle_seq"]), next_tranche_no, str(latest_date.date()), float(trigger), float(latest_low)),
                    )
                    rec = {"symbol": sym, "description": description, "tranche": next_tranche_no, "entry_date": str(latest_date.date()),
                           "entry_price": trigger, "ath": ath, "pct_below_ath": (trigger / ath - 1) * 100}
                    newly_opened.append(rec)
                    meets_criteria.append(rec)

        conn.commit()
        open_now = pd.read_sql_query("SELECT * FROM ath_positions WHERE status='OPEN'", conn)
    finally:
        conn.close()

    if not open_now.empty:
        current_close = {}
        for sym in open_now["symbol"].unique():
            df = prices[prices["symbol"] == sym]
            current_close[sym] = df["close"].iloc[-1] if not df.empty else None
        open_now["description"] = open_now["symbol"].map(meta)
        open_now["current_price"] = open_now["symbol"].map(current_close)
        open_now["unrealized_pct"] = (open_now["current_price"] / open_now["entry_price"] - 1) * 100
        open_now["current_drawdown_pct"] = (open_now["min_low"] / open_now["entry_price"] - 1) * 100
        open_now["days_held"] = open_now["entry_date"].apply(lambda d: (today - datetime.strptime(d, "%Y-%m-%d").date()).days)

    watchlist_df = pd.DataFrame(watchlist)
    if not watchlist_df.empty:
        watchlist_df = watchlist_df.sort_values("pct_below_ath").reset_index(drop=True)

    message = f"{len(newly_opened)} new tranche(s) opened, {len(newly_closed)} closed (check alerts), {len(open_now)} still open."
    return AthLiveResult(
        ok=True, message=message,
        newly_opened=pd.DataFrame(newly_opened), newly_closed=pd.DataFrame(newly_closed), open_positions=open_now,
        meets_criteria=pd.DataFrame(meets_criteria), watchlist=watchlist_df,
    )
