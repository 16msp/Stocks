"""Run the Minervini backtest over about 11 years of daily prices and store every trade in the database.

The app's own price store keeps only ~15 months (not enough for 200-day averages plus a 12-month RS rating across several market cycles),
so the long history is downloaded once into a local cache file (backtest_history.pkl, not committed) and reused on later runs.

    python scripts/run_minervini_backtest.py                 # use the cache (download it first if missing), write results to the database
    python scripts/run_minervini_backtest.py --refresh       # download the history again first
    python scripts/run_minervini_backtest.py --no-save       # only print the summary

Results go to the database that DATABASE_URL points to (local SQLite when it is not set).
"""

import argparse
import pickle
import sys
import time
from datetime import date
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from strategies import backtest_store, minervini_backtest, price_store, sector_scanner  # noqa: E402
from strategies.position_guide import NIFTY_TICKER  # noqa: E402

CACHE = ROOT / "backtest_history.pkl"
START = date(2015, 1, 1)


def universe() -> dict:
    """{symbol: sector text} for every stock in the app's sector lists."""
    out = {}
    for sector in sector_scanner.SECTORS:
        uni, _ = sector_scanner.load_universe(sector)
        for s in uni["Stock"]:
            out[s] = f"{out[s]}, {sector}" if s in out else sector
    return out


def download(symbols: list) -> dict:
    hist, end = {}, date.today()
    chunks = [symbols[i:i + price_store.CHUNK_SIZE] for i in range(0, len(symbols), price_store.CHUNK_SIZE)]
    for n, chunk in enumerate(chunks, 1):
        got = price_store._download(chunk, START, end)
        if len(got) < len(chunk) * 0.5:  # Yahoo sometimes drops a whole batch: one retry
            time.sleep(3)
            got = {**got, **price_store._download([s for s in chunk if s not in got], START, end)}
        hist.update(got)
        print(f"  downloaded {n}/{len(chunks)} batches, {len(hist)} stocks so far", flush=True)
    return hist


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--no-save", action="store_true")
    args = ap.parse_args()

    sectors = universe()
    print(f"{len(sectors)} stocks in the sector lists")
    if args.refresh or not CACHE.exists():
        print(f"Downloading history from {START} ...")
        hist = download(sorted(sectors) + [NIFTY_TICKER])
        with open(CACHE, "wb") as fh:
            pickle.dump(hist, fh)
    else:
        with open(CACHE, "rb") as fh:
            hist = pickle.load(fh)
    nifty = hist.pop(NIFTY_TICKER, None)
    if nifty is None:
        print("No Nifty history, cannot judge the market trend.")
        return 1
    hist = {s: h for s, h in hist.items() if s in sectors}
    print(f"{len(hist)} stocks with prices; Nifty {nifty.index.min().date()} to {nifty.index.max().date()}")

    t0 = time.time()
    trades = minervini_backtest.run(hist, nifty, sectors, progress=lambda k, n, s: print(f"  {k}/{n} stocks done, {s} signals", flush=True))
    print(f"Backtest took {time.time() - t0:.0f} s; {len(trades)} signals")
    if trades.empty:
        return 1
    up = trades[(trades["nifty_state"] == "Uptrend") & ~trades["trade_check"].str.startswith("Skip")]
    up = minervini_backtest.non_overlapping(up)
    closed = up[up["outcome"] != "Open"]
    print(f"Nifty uptrend, stop within 10%, one trade per stock at a time: {len(up)} trades, {int((up['outcome'] == 'Win').sum())} wins, "
          f"{int((up['outcome'] == 'Loss').sum())} losses, {int((up['outcome'] == 'Open').sum())} open; average R on closed trades {closed['r_multiple'].mean():+.2f}")
    if not args.no_save:
        params = {"target_r": minervini_backtest.TARGET_R, "min_liquidity_cr": minervini_backtest.MIN_LIQUIDITY_CR, "history_start": START.isoformat(),
                  "universe": "current sector lists", "entry": "close of the first breakout day", "template": "all 8 conditions"}
        run_id = backtest_store.save_run(trades, params, str(nifty.index.min().date()), str(nifty.index.max().date()), len(hist))
        print("Saved run", run_id)
    return 0


if __name__ == "__main__":
    sys.exit(main())
