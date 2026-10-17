"""Buy point, stop and risk for a Minervini-style base (VCP), worked out from the stored daily prices.

The rules are mechanical so they can be checked on a chart:
- Base: the stock's highest high of the last LOOKBACK sessions (before any recent breakout, ignoring the last PROBE_SESSIONS sessions' intraday probes) is the left peak; the base is everything since then.
  Under MIN_BASE_SESSIONS (3 weeks) there is no proper base. Base depth = the peak to the lowest low since.
- Pivot (buy point): that highest high. If price has already closed through the earlier highs within the last BREAKOUT_LOOKBACK sessions,
  the stock has broken out; the pivot is the high it broke through.
- Contractions: pullbacks of at least MIN_SWING in closing price inside the base. Shrinking = none of the last three is more than
  1.5 points deeper than the one before. Tightening = the last is under 8% and no more than 60% of the biggest.
- Stop: just under the low of the final pullback. It is NOT capped: if it is more than MAX_STOP_RISK from the entry the setup is a skip.
- Entry: pivot + 0.1% (for a stock already through the pivot, the latest close while it is within CHASE_LIMIT above the pivot).
- Breakout quality: the breakout day should trade at least BREAKOUT_VOL_X times the 50-day average volume and close in the top half of its range.
- Retesting: it broke out within the last BREAKOUT_LOOKBACK sessions but the latest close is back under the pivot.
- Extended: more than CHASE_LIMIT above the pivot.
This is a rule-based chart reading, not a prediction or advice.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

LOOKBACK = 150  # sessions searched for the base's left peak (about 30 weeks)
MIN_BASE_SESSIONS = 15
PROBE_SESSIONS = 5  # highs in the last few sessions without a closing breakout are probes, not the base's left peak
BREAKOUT_LOOKBACK = 10
MIN_SWING = 0.03
SHRINK_TOLERANCE = MIN_SWING / 2  # a pullback may be 1.5 points deeper than the one before and still count as not growing (noise)
GOOD_STOP_RISK = 0.08
MAX_STOP_RISK = 0.10
DEEP_BASE = 0.35
CHASE_LIMIT = 0.05
NEAR_PIVOT = 0.03
BREAKOUT_VOL_X = 1.4
MIN_CLOSE_LOCATION = 0.5
MIN_HISTORY = 70

COLUMNS = ["Verdict", "Status", "Trade check", "Pivot", "Entry", "Buy up to", "Stop", "Risk %", "Contractions", "Shrinking", "Tightening",
           "Volume dried up", "Base weeks", "Base depth", "Breakout note", "Breakout quality", "Volume to confirm", "To pivot", "Target 2R", "Target 3R"]
EMPTY = {c: np.nan for c in COLUMNS} | {"Verdict": "Not enough price history", "Status": "No data", "Trade check": "No data", "Contractions": "",
                                       "Shrinking": False, "Tightening": False, "Volume dried up": False, "Breakout note": "", "Breakout quality": False}


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


def position_size(entry: float, stop: float, capital: float, risk_pct: float, max_position_pct: float) -> dict:
    """Shares so that being stopped out costs about `risk_pct` of `capital`, limited to `max_position_pct` of capital in one stock."""
    out = {"Qty": 0, "Invested": 0.0, "Max loss": 0.0, "Sizing note": ""}
    if not (entry > stop > 0) or capital <= 0:
        out["Sizing note"] = "No position size: the stop is missing or not below the entry."
        return out
    by_risk = int(capital * risk_pct / (entry - stop))
    by_cap = int(capital * max_position_pct // entry)
    qty = max(0, min(by_risk, by_cap))
    out.update({"Qty": qty, "Invested": qty * entry, "Max loss": qty * (entry - stop)})
    out["Sizing note"] = ("capped by the maximum position size" if by_cap < by_risk else "set by your risk limit") if qty else "one share is already over your limits"
    return out


def plan(hist: pd.DataFrame) -> dict:
    """Entry plan for one stock from its OHLCV history (index = date)."""
    out = dict(EMPTY)
    h = hist.dropna(subset=["Close"])
    if len(h) < MIN_HISTORY:
        return out
    w = h.iloc[-LOOKBACK:]
    close, vol = w["Close"], w["Volume"]
    high = w["High"].where(w["High"].notna(), close)
    low = w["Low"].where(w["Low"].notna(), close)
    last = float(close.iloc[-1])
    avg50 = float(h["Volume"].iloc[-50:].mean())
    n = len(w)

    # has it already closed through the earlier highs of this window in the last few sessions?
    prior_high = high.shift(1).cummax()
    through = (close > prior_high) & (np.arange(n) >= 20)
    recent = np.flatnonzero(through.to_numpy()[-BREAKOUT_LOOKBACK:])
    broke = bool(len(recent))
    b = n - BREAKOUT_LOOKBACK + int(recent[0]) if broke else n  # first breakout day, or n if none
    pre_high, pre_low, pre_close, pre_vol = high.iloc[:b], low.iloc[:b], close.iloc[:b], vol.iloc[:b]

    # base: from the highest high before the breakout to the end of the pre-breakout stretch
    p = int(np.argmax(pre_high.to_numpy()[:max(len(pre_high) - PROBE_SESSIONS, 1)]))
    pivot = float(pre_high.iloc[p])
    base_len = len(pre_high) - p
    out["Pivot"], out["To pivot"] = pivot, last / pivot - 1.0
    out["Base weeks"] = base_len / 5.0
    out["Base depth"] = 1.0 - float(pre_low.iloc[p:].min()) / pivot
    entry = round(last if broke and 0 <= last / pivot - 1 <= CHASE_LIMIT else pivot * 1.001, 2)
    out["Entry"], out["Buy up to"] = entry, round(pivot * (1 + CHASE_LIMIT), 2)
    dist = last / pivot - 1.0

    base_close, base_low = pre_close.iloc[p:], pre_low.iloc[p:]
    pulls = _pullbacks(base_close, MIN_SWING) if len(base_close) > 3 else []
    depths = [d for d, _, _ in pulls]
    out["Contractions"] = " > ".join(f"{d:.0%}" for d in depths[-4:])
    last3 = depths[-3:]
    out["Shrinking"] = bool(len(last3) >= 2 and all(y <= x + SHRINK_TOLERANCE for x, y in zip(last3, last3[1:])))
    out["Tightening"] = bool(len(depths) >= 2 and depths[-1] <= 0.6 * max(depths) and depths[-1] <= 0.08)
    pre_recent = pre_vol.iloc[p:]
    out["Volume dried up"] = bool(avg50 > 0 and len(pre_recent) >= 5 and pre_recent.iloc[-10:].mean() < 0.8 * avg50)
    out["Volume to confirm"] = avg50 * BREAKOUT_VOL_X

    # stop: under the low of the final pullback (from its peak to the end of the base)
    final_from = pulls[-1][1] if pulls else max(0, len(base_low) - 10)
    structural = round(float(base_low.iloc[final_from:].min()) * 0.99, 2)
    stop = structural if structural < entry else round(entry * (1 - GOOD_STOP_RISK), 2)
    risk = 1 - stop / entry
    out["Stop"], out["Risk %"] = stop, risk
    out["Target 2R"], out["Target 3R"] = round(entry + 2 * (entry - stop), 2), round(entry + 3 * (entry - stop), 2)

    # breakout quality
    day_vol_x = np.nan
    if broke:
        day = w.iloc[b]
        day_vol_x = float(day["Volume"]) / avg50 if avg50 > 0 else np.nan
        rng = float(high.iloc[b] - low.iloc[b])
        loc = (float(close.iloc[b]) - float(low.iloc[b])) / rng if rng > 0 else 1.0
        margin = float(close.iloc[b]) / float(prior_high.iloc[b]) - 1.0
        days_ago = n - 1 - b
        out["Breakout quality"] = bool(day_vol_x >= BREAKOUT_VOL_X and loc >= MIN_CLOSE_LOCATION)
        out["Breakout note"] = (f"breakout day {'today' if not days_ago else f'{days_ago} day(s) ago'}: volume {day_vol_x:.1f}x average, closed in the "
                                f"{'top' if loc >= 0.5 else 'bottom'} {max(loc, 1 - loc):.0%} of its range, {margin:+.1%} vs the pivot")
        pivot = float(prior_high.iloc[b])  # the high it actually broke through
        out["Pivot"], out["To pivot"] = pivot, last / pivot - 1.0
        dist = last / pivot - 1.0
        entry = round(last if 0 <= dist <= CHASE_LIMIT else pivot * 1.001, 2)
        out["Entry"], out["Buy up to"] = entry, round(pivot * (1 + CHASE_LIMIT), 2)
        stop = structural if structural < entry else round(entry * (1 - GOOD_STOP_RISK), 2)
        risk = 1 - stop / entry
        out["Stop"], out["Risk %"] = stop, risk
        out["Target 2R"], out["Target 3R"] = round(entry + 2 * (entry - stop), 2), round(entry + 3 * (entry - stop), 2)
        out["Volume dried up"] = bool(avg50 > 0 and len(pre_vol) >= 5 and pre_vol.iloc[-10:].mean() < 0.8 * avg50)

    wide = f" Skip: the chart's stop is {risk:.0%} away (limit {MAX_STOP_RISK:.0%})." if risk > MAX_STOP_RISK else ""
    deep = f" Deep base ({out['Base depth']:.0%})." if out["Base depth"] > DEEP_BASE else ""
    out["Trade check"] = ("Skip - stop too far" if risk > MAX_STOP_RISK else "OK, but stop is wide" if risk > GOOD_STOP_RISK else "OK")

    if base_len < MIN_BASE_SESSIONS:
        out["Status"], out["Trade check"] = "No base", "No base yet"
        out["Verdict"] = f"No proper base yet: the high was only {base_len} session(s) ago (needs {MIN_BASE_SESSIONS}+). Not a VCP buy point."
    elif broke and dist > CHASE_LIMIT:
        out["Status"], out["Trade check"] = "Extended", "Skip - extended"
        out["Verdict"] = f"Too late - {dist:.0%} above the pivot. Wait for a new base."
    elif broke and dist < 0:
        out["Status"] = "Retesting"
        out["Verdict"] = (f"Broke out but closed back {-dist:.1%} below the pivot - a retest. Buy only on a close back above ₹{out['Entry']:,.2f}; "
                          f"the breakout has failed if it closes under ₹{stop:,.2f}.{wide}{deep}")
    elif broke:
        out["Status"] = "Broke out"
        good = out["Breakout quality"]
        out["Verdict"] = ((f"Triggered - in the buy zone up to ₹{out['Buy up to']:,.2f}." if good else
                           "Broke out but weakly (volume or close not strong enough) - smaller size or wait for a retest.") + wide + deep)
    elif dist >= -NEAR_PIVOT:
        out["Status"] = "Near pivot"
        out["Verdict"] = (f"Ready - buy only on a move through ₹{out['Entry']:,.2f} with {out['Volume to confirm']:,.0f}+ shares and a close in the top half of the day's range."
                          + wide + deep)
    else:
        out["Status"] = "Building"
        out["Verdict"] = f"Not ready - {-dist:.0%} below the pivot. Set an alert at ₹{out['Entry']:,.2f}.{wide}{deep}"
    return out
