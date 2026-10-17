"""Quarterly-results timing: the Indian results season, and (on request) each stock's next results date from Yahoo Finance.

Yahoo's date is often missing or only an estimate for Indian small caps, so a blank means "check the company's announcement",
never "no results coming".
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import date

# (start month, start day, end month, end day, label): when most listed companies report (SEBI allows 45 days after a quarter, 60 for March)
SEASONS = [(1, 10, 2, 14, "December quarter"), (4, 15, 5, 31, "March quarter and full year"), (7, 10, 8, 14, "June quarter"), (10, 10, 11, 14, "September quarter")]


def results_season(today: date | None = None) -> str:
    """Name of the quarter being reported if `today` is inside a results season, else ''."""
    t = today or date.today()
    for m1, d1, m2, d2, label in SEASONS:
        if (t.month, t.day) >= (m1, d1) and (t.month, t.day) <= (m2, d2):
            return label
    return ""


def _one(symbol: str):
    try:
        import yfinance as yf

        cal = yf.Ticker(f"{symbol}.NS").calendar
        dates = [d for d in (cal.get("Earnings Date") or []) if hasattr(d, "year")] if isinstance(cal, dict) else []
        future = sorted(d for d in dates if d >= date.today())
        return symbol, (future[0] if future else None)
    except Exception:
        return symbol, None


def next_results(symbols: list) -> dict:
    """{symbol: next results date from Yahoo, or None}. Looks up each symbol (about a second each), a few at a time."""
    with ThreadPoolExecutor(max_workers=6) as pool:
        return dict(pool.map(_one, list(symbols)))


def describe(d, today: date | None = None) -> str:
    """One line for a card or table: when results are due and whether that is too close to enter."""
    t = today or date.today()
    if d is None:
        return "Results date not known (Yahoo has none) - check the company's announcement before entering"
    days = (d - t).days
    soon = " - too close: a gap either way can ignore the stop" if days <= 14 else ""
    return f"Results expected {d:%d %b %Y} (Yahoo estimate), in {days} day(s){soon}"
