#!/usr/bin/env python3
"""
Live NSE (India) ETF list sorted by weekly traded volume.

Data sources:
  - ETF universe + today's snapshot: NSE India public API (nseindia.com/api/etf)
  - 5-session (weekly) volume history: Yahoo Finance via yfinance (<SYMBOL>.NS)

Usage:
  python nse_etf_weekly_volume.py                 # top 25, prints table + writes CSV
  python nse_etf_weekly_volume.py --top 50
  python nse_etf_weekly_volume.py --out my.csv
"""

import argparse
import sys
import time

import pandas as pd
import requests
import yfinance as yf
from tabulate import tabulate

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

CHUNK_SIZE = 50


def fetch_nse_etf_list() -> pd.DataFrame:
    """Pull the live list of NSE-listed ETFs with today's snapshot data."""
    session = requests.Session()
    session.headers.update(HEADERS)
    # Priming request establishes cookies NSE requires before the API call works.
    session.get(NSE_HOME, timeout=15)
    resp = session.get(NSE_ETF_API, timeout=15)
    resp.raise_for_status()
    rows = resp.json()["data"]

    df = pd.DataFrame(rows)[["symbol", "assets", "ltP", "qty", "nav"]]
    df = df.rename(
        columns={
            "assets": "category",
            "ltP": "last_price",
            "qty": "today_volume",
            "nav": "nav",
        }
    )
    for col in ("last_price", "today_volume", "nav"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def fetch_weekly_volumes(symbols: list[str]) -> pd.Series:
    """Sum the last 5 trading sessions' volume per symbol via Yahoo Finance."""
    yahoo_symbols = [f"{s}.NS" for s in symbols]
    weekly = {}

    for i in range(0, len(yahoo_symbols), CHUNK_SIZE):
        chunk = yahoo_symbols[i : i + CHUNK_SIZE]
        for attempt in range(3):
            try:
                data = yf.download(
                    chunk,
                    period="10d",
                    interval="1d",
                    group_by="ticker",
                    threads=True,
                    progress=False,
                    auto_adjust=False,
                )
                break
            except Exception:
                time.sleep(2)
        else:
            data = None

        if data is None or data.empty:
            continue

        for ysym in chunk:
            base = ysym[:-3]  # strip ".NS"
            try:
                if len(chunk) == 1:
                    vol = data["Volume"].dropna()
                else:
                    vol = data[ysym]["Volume"].dropna()
                weekly[base] = int(vol.tail(5).sum())
            except (KeyError, TypeError):
                continue

    return pd.Series(weekly, name="weekly_volume")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--top", type=int, default=25, help="rows to display (default 25)")
    parser.add_argument(
        "--out", default="nse_etf_weekly_volume.csv", help="CSV output path"
    )
    args = parser.parse_args()

    print("Fetching live NSE ETF list...", file=sys.stderr)
    etf_df = fetch_nse_etf_list()
    print(f"  {len(etf_df)} ETFs found on NSE.", file=sys.stderr)

    print("Fetching weekly (last 5 sessions) volume from Yahoo Finance...", file=sys.stderr)
    weekly = fetch_weekly_volumes(etf_df["symbol"].tolist())

    result = etf_df.set_index("symbol").join(weekly).reset_index()
    result = result.dropna(subset=["weekly_volume"])
    result["weekly_volume"] = result["weekly_volume"].astype(int)
    result = result.sort_values("weekly_volume", ascending=False).reset_index(drop=True)

    result.to_csv(args.out, index=False)
    print(f"Saved full sorted list to {args.out}\n", file=sys.stderr)

    display = result.head(args.top).copy()
    display.index += 1
    print(
        tabulate(
            display[
                ["symbol", "category", "last_price", "today_volume", "weekly_volume"]
            ],
            headers=["Symbol", "Category", "LTP", "Today Vol", "Weekly Vol"],
            tablefmt="github",
            floatfmt=",.2f",
            intfmt=",",
        )
    )


if __name__ == "__main__":
    main()
