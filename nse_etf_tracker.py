#!/usr/bin/env python3
"""
NSE (India) ETF weekly momentum tracker - CLI.

  fetch    Pull the live NSE ETF list + daily close/volume history and store it
           in a local SQLite DB (nse_etf_data.db). Incremental: only fetches
           days not already stored, so re-running doesn't re-download everything.

  analyze  Read the stored history, bucket it into ISO calendar weeks, and rank
           ETFs by volume trend + price trend to surface short-term momentum
           candidates.

For a point-and-click UI over the same data/logic, run: streamlit run app.py

Usage:
  python nse_etf_tracker.py fetch
  python nse_etf_tracker.py analyze
  python nse_etf_tracker.py analyze --weeks 4 --top 20
"""

import argparse
import sys

from tabulate import tabulate

from strategies import nse_etf_momentum as strategy


def cmd_fetch(args: argparse.Namespace) -> None:
    result = strategy.fetch(progress=lambda msg: print(msg, file=sys.stderr))
    if result.fetched:
        print(f"Stored in {strategy.DB_PATH}", file=sys.stderr)


def cmd_analyze(args: argparse.Namespace) -> None:
    result = strategy.analyze(weeks=args.weeks, top=args.top, min_volume=args.min_volume)
    if not result.ok:
        print(result.message, file=sys.stderr)
        return

    if not result.bad_ticks.empty:
        print(
            f"Dropped {len(result.bad_ticks)} suspected bad tick(s) "
            f"(>{int(strategy.BAD_TICK_THRESHOLD * 100)}% single-day move, likely data glitch):",
            file=sys.stderr,
        )
        for _, r in result.bad_ticks.iterrows():
            print(f"  {r['symbol']} on {r['date'].date()} close={r['close']}", file=sys.stderr)

    out_cols = [
        "symbol", "category", "signal", "liquid",
        "prev_week_close", "this_week_close", "price_change_pct",
        "prev_week_volume", "this_week_volume", "volume_change_pct",
    ]
    result.full[out_cols].to_csv(args.out, index=False)
    print(
        f"{result.message} Full ranked list (incl. illiquid) saved to {args.out}\n"
        f"Rankings below exclude ETFs with prev-week volume < {args.min_volume:,} shares "
        f"(too thin for a meaningful % move).\n",
        file=sys.stderr,
    )

    def show(df, title):
        print(f"\n== {title} ==")
        if df.empty:
            print("(none)")
            return
        disp = df[["symbol", "category", "price_change_pct", "volume_change_pct", "this_week_close"]].copy()
        disp.index = range(1, len(disp) + 1)
        print(
            tabulate(
                disp,
                headers=["Symbol", "Category", "Price %", "Volume %", "Close"],
                tablefmt="github",
                floatfmt=",.2f",
            )
        )

    show(result.bullish, "Bullish momentum candidates (rising volume + rising price)")
    show(result.caution, "Caution - heavy volume with falling price")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    fetch_p = sub.add_parser("fetch", help="fetch live data and store it locally (incremental)")
    fetch_p.set_defaults(func=cmd_fetch)

    analyze_p = sub.add_parser("analyze", help="analyze stored data for weekly volume/price trend")
    analyze_p.add_argument("--weeks", type=int, default=2, help="weeks of history to compare (default 2: this vs last)")
    analyze_p.add_argument("--top", type=int, default=15, help="rows per signal bucket to display (default 15)")
    analyze_p.add_argument("--out", default="nse_etf_trend.csv", help="CSV output path for full ranked list")
    analyze_p.add_argument(
        "--min-volume", type=int, default=5000,
        help="minimum prior-week volume (shares) to be included in the ranked signal tables (default 5000)",
    )
    analyze_p.set_defaults(func=cmd_analyze)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
