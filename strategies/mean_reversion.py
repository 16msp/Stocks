"""Mean-reversion strategy: buy a stock in a long-term uptrend after a short, one-sided sell-off, sell on the bounce.

The rule (the one that was backtested over Oct 2015 - Sep 2026 on the stored stock list):
- Setup, judged on the day's close: close above the 200-day average, RSI(2) below RSI_MAX, average traded value at least MIN_VALUE_CR crore a day.
  RSI(2) is the 2-day RSI (Wilder-style smoothing with a 2-day span): it is low after several down days in a row, whatever their size.
  "Strong" setups also fell at least STRONG_DROP over the last 3 sessions.
- Entry: the NEXT session's open (the signal is only known after the close).
- Exit, whichever comes first: a session that closes above its 5-day average, or MAX_HOLD sessions (the entry session counts as the first);
  sold at the open of the session after that. No price stop: a stop made results worse at every level tested.
- Portfolio: SLOTS positions of equal size, one position per stock, lowest RSI(2) first when more stocks signal than slots are free.

Paper trading here follows the same timing: a buy is an order for the next session's open, filled (by a button press) once that session's prices are stored,
and a sell is recorded at the open of the session after the exit signal. Nothing is filled or sold automatically.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

RSI_MAX = 5.0
STRONG_DROP = 0.08
MIN_VALUE_CR = 5.0
SMA_LONG = 200
SMA_EXIT = 5
MAX_HOLD = 7
SLOTS = 10
BAD_MOVE_DOWN, BAD_MOVE_UP = -0.35, 0.60  # a one-day move beyond these in the last sessions is treated as a bad print / corporate action
MIN_SESSIONS = SMA_LONG + 5

# Backtest numbers quoted on the page (from the analysis scripts; not recomputed here). After tax = 20.8% on yearly net profit.
BACKTEST_ROWS = [
    {"Rule": "RSI(2) < 5", "Trades": 5671, "Win rate": 61.7, "Avg per trade, net of costs": 0.56, "CAGR before tax": 16.3, "CAGR after tax": 13.5, "Max drawdown": -44.8},
    {"Rule": "RSI(2) < 5 and 3-day drop of 8%+ (Strong)", "Trades": 1995, "Win rate": 62.0, "Avg per trade, net of costs": 1.93, "CAGR before tax": 20.9, "CAGR after tax": 17.8, "Max drawdown": -31.2},
    {"Rule": "Strong, sideways market only", "Trades": 604, "Win rate": 64.4, "Avg per trade, net of costs": None, "CAGR before tax": 13.8, "CAGR after tax": 12.2, "Max drawdown": -21.3},
]


def rsi2(close: pd.Series) -> pd.Series:
    """2-day RSI, exponential smoothing with alpha 1/2 (the same as the backtest)."""
    d = close.diff()
    up, dn = d.clip(lower=0), -d.clip(upper=0)
    au = up.ewm(alpha=0.5, adjust=False, min_periods=2).mean()
    ad = dn.ewm(alpha=0.5, adjust=False, min_periods=2).mean()
    return 100 - 100 / (1 + au / ad.replace(0, np.nan))


def round_trip_cost(value_cr: float) -> float:
    """Estimated cost of one buy plus one sell as a fraction: 0.25% statutory (STT 0.1% each side, stamp, exchange, DP) plus slippage by liquidity."""
    slip = 0.0010 if value_cr >= 25 else 0.0020 if value_cr >= 5 else 0.0040
    return 0.0025 + slip


def evaluate(history: pd.DataFrame) -> dict | None:
    """The strategy's numbers for the latest bar of one stock's OHLCV, or None if there is too little history or a bad print in the last sessions."""
    if history is None or history.empty:
        return None
    h = history.dropna(subset=["Close"])
    c = h["Close"]
    if len(c) < MIN_SESSIONS:
        return None
    moves = c.pct_change().iloc[-6:]
    if (moves < BAD_MOVE_DOWN).any() or (moves > BAD_MOVE_UP).any():
        return None
    r2 = rsi2(c)
    value_cr = float((c * h["Volume"]).iloc[-20:].mean() / 1e7)
    sma200 = float(c.iloc[-SMA_LONG:].mean())
    return {
        "Date": c.index[-1], "Close": float(c.iloc[-1]), "RSI2": float(r2.iloc[-1]), "Drop 3D": float(c.iloc[-1] / c.iloc[-4] - 1),
        "vs 200DMA": float(c.iloc[-1] / sma200 - 1), "SMA 5": float(c.iloc[-SMA_EXIT:].mean()), "Value Cr": value_cr,
        "Last closes": ", ".join(f"{x:,.1f}" for x in c.iloc[-4:]),
    }


def tier_of(rsi: float, drop3: float) -> str:
    if rsi < RSI_MAX and drop3 <= -STRONG_DROP:
        return "Strong"
    return "Basic" if rsi < RSI_MAX else "Loose"


def signals(histories: dict, rsi_max: float = RSI_MAX, min_drop: float = 0.0, min_value_cr: float = MIN_VALUE_CR) -> pd.DataFrame:
    """Stocks whose latest bar satisfies the setup, lowest RSI(2) first (Strong before Basic before Loose).
    `min_drop`: required 3-day fall (0.08 = at least 8%). Stocks whose last bar is older than the newest bar of the whole list are left out (stale data)."""
    rows = []
    for sym, h in histories.items():
        if sym.startswith("^"):
            continue
        e = evaluate(h)
        if e is None:
            continue
        rows.append({"Stock": sym, **e})
    if not rows:
        return pd.DataFrame(columns=["Stock", "Date", "Close", "RSI2", "Drop 3D", "vs 200DMA", "SMA 5", "Value Cr", "Last closes", "Tier", "Est. cost %"])
    t = pd.DataFrame(rows)
    t = t[t["Date"] == t["Date"].max()]
    t = t[(t["vs 200DMA"] > 0) & (t["RSI2"] < rsi_max) & (t["Drop 3D"] <= -min_drop) & (t["Value Cr"] >= min_value_cr)].copy()
    t["Tier"] = [tier_of(r, d) for r, d in zip(t["RSI2"], t["Drop 3D"])]
    t["Est. cost %"] = [round_trip_cost(v) * 100 for v in t["Value Cr"]]
    t["_o"] = t["Tier"].map({"Strong": 0, "Basic": 1, "Loose": 2})
    return t.sort_values(["_o", "RSI2"]).drop(columns="_o").reset_index(drop=True)


def entry_fill(history: pd.DataFrame, signal_session) -> tuple | None:
    """(session date, open price) of the first stored session after `signal_session`, or None if that session is not stored yet."""
    if history is None or history.empty:
        return None
    after = history[(history.index > pd.Timestamp(signal_session)) & history["Open"].notna()]
    if after.empty:
        return None
    return after.index[0].date(), float(after["Open"].iloc[0])


def exit_status(history: pd.DataFrame, entry_session) -> dict:
    """Where an open position stands against the exit rule. `entry_session` = the session whose open was the entry (it counts as session 1).
    Returns: day (sessions held so far, the entry session = 1), close, sma5, state ('hold' | 'bounce' | 'time'), signal_session (the session whose close
    triggered the exit), sell_session / sell_price (the open of the next session, None if not stored yet)."""
    h = history.dropna(subset=["Close"])
    c = h["Close"]
    sma5 = c.rolling(SMA_EXIT).mean()
    pos0 = c.index.get_indexer([pd.Timestamp(entry_session)])[0]
    out = {"day": 0, "close": float("nan"), "sma5": float("nan"), "state": "hold", "signal_session": None, "sell_session": None, "sell_price": None}
    if pos0 < 0:
        return out
    last = len(c) - 1
    for k in range(1, MAX_HOLD + 1):
        pos = pos0 + k - 1
        if pos > last:
            break
        out.update(day=k, close=float(c.iloc[pos]), sma5=float(sma5.iloc[pos]))
        if c.iloc[pos] > sma5.iloc[pos] or k == MAX_HOLD:
            out.update(state="bounce" if c.iloc[pos] > sma5.iloc[pos] else "time", signal_session=c.index[pos].date())
            if pos + 1 <= last and pd.notna(h["Open"].iloc[pos + 1]):
                out.update(sell_session=c.index[pos + 1].date(), sell_price=float(h["Open"].iloc[pos + 1]))
            break
    return out
