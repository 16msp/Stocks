"""Buy point, stop and risk for a Minervini-style base (VCP), worked out from the stored daily prices.

The rules are mechanical so they can be checked on a chart:
- Look at the last BASE_SESSIONS sessions (about 13 weeks).
- Pivot (buy point): the highest high of that window. If price has already closed above the window's earlier highs within the last
  BREAKOUT_LOOKBACK sessions, the pivot is the high it broke through (the stock has broken out).
- Entry: pivot + 0.1% (for a stock already through the pivot, today's close, as long as it is within CHASE_LIMIT of the pivot).
- Chase limit: more than CHASE_LIMIT above the pivot is "extended" - wait for a new base instead of buying.
- Stop: just under the lowest low of the last STOP_LOOKBACK (15) sessions before the breakout, but never more than MAX_RISK below the entry (Minervini's 7-8% rule).
- Contractions: pullbacks of at least MIN_SWING in closing price. A tightening base has a last pullback well under the biggest one and under 8%.
- Volume: it should dry up in the base, and the breakout day should trade at least BREAKOUT_VOL_X times the 50-day average volume.
This is a rule-based chart reading, not a prediction or advice.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

BASE_SESSIONS = 65
BREAKOUT_LOOKBACK = 10
MIN_SWING = 0.03
MAX_RISK = 0.08
CHASE_LIMIT = 0.05
NEAR_PIVOT = 0.03
BREAKOUT_VOL_X = 1.4
MIN_HISTORY = 70
STOP_LOOKBACK = 15  # sessions

COLUMNS = ["Verdict", "Status", "Pivot", "Entry", "Buy up to", "Stop", "Risk %", "Stop note", "Contractions", "Tightening", "Volume dried up",
           "Breakout volume", "Volume to confirm", "To pivot", "Target 2R", "Target 3R"]
EMPTY = {c: np.nan for c in COLUMNS} | {"Verdict": "Not enough price history", "Status": "No data", "Stop note": "", "Contractions": "",
                                       "Tightening": False, "Volume dried up": False, "Breakout volume": ""}


def _pullbacks(close: pd.Series, min_swing: float) -> list:
    """Pullbacks along `close`, oldest first, as (depth, peak position, trough position); depth is a positive fraction.
    The last one may still be in progress."""
    c = close.to_numpy()
    out, peak_i, trough_i, direction = [], 0, 0, 1
    for i in range(1, len(c)):
        if direction == 1:
            if c[i] > c[peak_i]:
                peak_i = i
            elif c[i] <= c[peak_i] * (1 - min_swing):
                direction, trough_i = -1, i
        else:
            if c[i] < c[trough_i]:
                trough_i = i
            elif c[i] >= c[trough_i] * (1 + min_swing):
                out.append((1 - c[trough_i] / c[peak_i], peak_i, trough_i))
                direction, peak_i = 1, i
    if direction == -1:
        out.append((1 - c[trough_i] / c[peak_i], peak_i, trough_i))
    return out


def plan(hist: pd.DataFrame) -> dict:
    """Entry plan for one stock from its OHLCV history (index = date)."""
    out = dict(EMPTY)
    h = hist.dropna(subset=["Close"])
    if len(h) < MIN_HISTORY:
        return out
    w = h.iloc[-BASE_SESSIONS:]
    close, vol = w["Close"], w["Volume"]
    high = w["High"].where(w["High"].notna(), close)
    low = w["Low"].where(w["Low"].notna(), close)
    last = float(close.iloc[-1])
    avg50 = float(h["Volume"].iloc[-50:].mean())

    # has it already closed through the earlier highs of this window in the last few sessions?
    prior_high = high.shift(1).cummax()
    through = (close > prior_high) & (np.arange(len(w)) >= 20)
    recent = np.flatnonzero(through.to_numpy()[-BREAKOUT_LOOKBACK:])
    if len(recent):
        b = len(w) - BREAKOUT_LOOKBACK + int(recent[0])  # first breakout day
        pivot, broke = float(prior_high.iloc[b]), True
        base_close, base_low = close.iloc[:b], low.iloc[:b]
        days_ago = len(w) - 1 - b
        bo_vol = float(vol.iloc[b:].max()) / avg50 if avg50 > 0 else np.nan
        out["Breakout volume"] = f"{bo_vol:.1f}x average ({days_ago} day(s) ago)" if days_ago else f"{bo_vol:.1f}x average (today)"
    else:
        pivot, broke = float(high.max()), False
        base_close, base_low = close, low
        bo_vol = np.nan

    dist = last / pivot - 1.0
    out["Pivot"], out["To pivot"] = pivot, dist
    entry = round(last if broke and dist <= CHASE_LIMIT else pivot * 1.001, 2)
    out["Entry"], out["Buy up to"] = entry, round(pivot * (1 + CHASE_LIMIT), 2)

    pulls = _pullbacks(base_close, MIN_SWING) if len(base_close) > 3 else []
    depths = [d for d, _, _ in pulls]
    out["Contractions"] = " > ".join(f"{d:.0%}" for d in depths[-4:])
    out["Tightening"] = bool(len(depths) >= 2 and depths[-1] <= 0.6 * max(depths) and depths[-1] <= 0.08)

    # stop: under the lowest low of the STOP_LOOKBACK sessions before the breakout (or before today if it has not broken out)
    last_low = float(base_low.iloc[-STOP_LOOKBACK:].min())
    structural = round(last_low * 0.99, 2)
    floor = round(entry * (1 - MAX_RISK), 2)
    stop = max(structural, floor)
    out["Stop"], out["Risk %"] = stop, 1 - stop / entry
    out["Stop note"] = "under the 15-session low" if structural >= floor else f"15-session low is {1 - structural / entry:.0%} below the entry, so capped at {MAX_RISK:.0%}"
    risk = entry - stop
    out["Target 2R"], out["Target 3R"] = round(entry + 2 * risk, 2), round(entry + 3 * risk, 2)

    pre = vol.iloc[:b] if broke else vol
    out["Volume dried up"] = bool(avg50 > 0 and len(pre) >= 5 and pre.iloc[-10:].mean() < 0.8 * avg50)
    out["Volume to confirm"] = avg50 * BREAKOUT_VOL_X

    if broke and dist > CHASE_LIMIT:
        out["Status"], out["Verdict"] = "Extended", f"Too late - {dist:.0%} above the pivot. Wait for a new base."
    elif broke:
        ok = bool(bo_vol >= BREAKOUT_VOL_X)
        out["Status"] = "Broke out"
        out["Verdict"] = (f"Triggered - in the buy zone up to ₹{out['Buy up to']:,.2f}" if ok else
                          f"Broke out on light volume ({bo_vol:.1f}x) - weaker signal, size small or wait for a retest")
    elif dist >= -NEAR_PIVOT:
        out["Status"] = "Near pivot"
        out["Verdict"] = f"Ready - buy only on a move through ₹{out['Entry']:,.2f} with volume of {out['Volume to confirm']:,.0f}+ shares"
    else:
        out["Status"], out["Verdict"] = "Building", f"Not ready - {-dist:.0%} below the pivot. Set an alert at ₹{out['Entry']:,.2f}."
    return out
