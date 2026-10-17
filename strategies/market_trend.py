"""Is the broad market (Nifty 50) in an uptrend? Minervini only buys breakouts when it is.

Uptrend = Nifty above its 50-day average, 50-day above 200-day, and the 200-day average rising over the last month.
Downtrend = Nifty below its 200-day average. Anything in between is "mixed".
Uses the daily closes already stored for the app (symbol ^NSEI).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from strategies import price_store

NIFTY = "^NSEI"


def nifty_trend() -> dict:
    """{'state': 'Uptrend'|'Mixed'|'Downtrend'|'Unknown', 'detail': str, ...numbers} from the stored Nifty closes."""
    hist = price_store.load_history([NIFTY]).get(NIFTY)
    if hist is None:
        return {"state": "Unknown", "detail": "No Nifty prices are stored, so the market trend could not be checked."}
    c = hist["Close"].dropna()
    if len(c) < 221:
        return {"state": "Unknown", "detail": "Not enough Nifty history to judge the trend."}
    last = float(c.iloc[-1])
    sma50, sma200 = float(c.iloc[-50:].mean()), float(c.iloc[-200:].mean())
    sma200_1m = float(c.iloc[-221:-21].mean())
    high52 = float(c.iloc[-252:].max())
    rising = sma200 > sma200_1m
    if last > sma50 > sma200 and rising:
        state = "Uptrend"
    elif last < sma200:
        state = "Downtrend"
    else:
        state = "Mixed"
    detail = (f"Nifty {last:,.0f} is {last / sma50 - 1:+.1%} vs its 50-day average and {last / sma200 - 1:+.1%} vs the 200-day average "
              f"({'rising' if rising else 'not rising'} over the last month), {last / high52 - 1:+.1%} from its 52-week high. As of {c.index[-1].date()}.")
    return {"state": state, "detail": detail, "last": last, "date": c.index[-1].date()}
