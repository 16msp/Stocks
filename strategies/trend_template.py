"""Mark Minervini's Trend Template: the eight conditions a stock should meet to be in a Stage 2 uptrend.

1. Price above the 150-day and 200-day moving averages.
2. 150-day above 200-day.
3. 200-day rising for at least a month (it prefers 4-5 months).
4. 50-day above both the 150-day and 200-day.
5. Price above the 50-day.
6. Price at least 30% above the 52-week low.
7. Price within 25% of the 52-week high.
8. Relative-strength rating of at least 70 (80-90s are best).

Everything is worked out from closing prices (the app stores daily closes, not intraday highs and lows). The RS rating
is the usual 40% / 20% / 20% / 20% blend of the 3, 6, 9 and 12-month returns, ranked 1-99 against the stocks in this
app's sector lists, so it is relative to this universe, not to every NSE stock.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

DEFAULTS = {"near_high": 0.25, "above_low": 0.30, "min_rs": 70}

CRITERIA = {
    "C1": "Price above 150 & 200-day",
    "C2": "150-day above 200-day",
    "C3": "200-day rising (1M+)",
    "C4": "50-day above 150 & 200-day",
    "C5": "Price above 50-day",
    "C6": "30%+ above 52W low",
    "C7": "Within 25% of 52W high",
    "C8": "RS rating 70+",
}


def rs_rating(table: pd.DataFrame) -> pd.Series:
    """1-99 rank of the weighted 3/6/9/12-month return among the rows; NaN where any of the four returns is missing."""
    p = table[["Perf 3M", "Perf 6M", "Perf 9M", "Perf 12M"]]
    score = 0.4 * p["Perf 3M"] + 0.2 * p["Perf 6M"] + 0.2 * p["Perf 9M"] + 0.2 * p["Perf 12M"]
    score = score.where(p.notna().all(axis=1))
    return (score.rank(pct=True) * 98 + 1).round()


def evaluate(table: pd.DataFrame, near_high: float = 0.25, above_low: float = 0.30, min_rs: float = 70) -> pd.DataFrame:
    """Add the RS rating, one True/False column per criterion (C1..C8) and 'Criteria met' (0-8).
    Stocks with under 200 sessions of history cannot be judged and get 'Criteria met' = NaN."""
    t = table.copy()
    t["RS Rating"] = rs_rating(t)
    cmp_ = t["CMP"]
    t["C1"] = (cmp_ > t["SMA 150"]) & (cmp_ > t["SMA 200"])
    t["C2"] = t["SMA 150"] > t["SMA 200"]
    t["C3"] = t["SMA 200"] > t["SMA 200 1M Ago"]
    t["C4"] = (t["SMA 50"] > t["SMA 150"]) & (t["SMA 50"] > t["SMA 200"])
    t["C5"] = cmp_ > t["SMA 50"]
    t["C6"] = t["Off 52W Low"] >= above_low
    t["C7"] = t["52W High Drop"] >= -near_high
    t["C8"] = t["RS Rating"] >= min_rs
    flags = list(CRITERIA)
    t["Criteria met"] = t[flags].sum(axis=1).astype(float)
    judged = t["SMA 200 1M Ago"].notna() & t["SMA 150"].notna() & t["SMA 50"].notna() & cmp_.notna()
    t.loc[~judged, "Criteria met"] = np.nan
    t["Missing"] = t.apply(lambda r: "" if pd.isna(r["Criteria met"]) else ", ".join(CRITERIA[c] for c in flags if not r[c]), axis=1)
    t["200DMA up 4M"] = (t["SMA 200"] > t["SMA 200 4M Ago"]).where(t["SMA 200 4M Ago"].notna())
    return t
