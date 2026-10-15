"""Daily job: bring the price store up to date for everything the app looks at.

Symbols = every sector in strategies/sector_scanner.SECTORS + the stocks in your
Open Positions sheet (if POSITIONS_SHEET_URL is set) + Nifty. Only sessions newer
than what's already stored are downloaded.

Run locally:      python scripts/update_prices.py            (uses the local SQLite file)
Run against cloud: DATABASE_URL=postgresql://... python scripts/update_prices.py
Add --force to re-check Yahoo even if the store looks current.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from strategies import position_guide as pg  # noqa: E402
from strategies import price_store, sector_scanner  # noqa: E402
from strategies.config import get_setting  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    symbols = {pg.NIFTY_TICKER}
    for sector in sector_scanner.SECTORS:
        universe, source = sector_scanner.load_universe(sector, refresh=True)
        print(f"{sector}: {len(universe)} stocks ({source})")
        symbols.update(universe["Stock"])

    sheet = get_setting("POSITIONS_SHEET_URL")
    if sheet:
        try:
            held = set(pg.load_open_positions(sheet)["Stock"].dropna().unique())
            print(f"Holdings: {len(held)} stocks")
            symbols.update(held)
        except Exception as exc:  # the sector stocks are still worth updating
            print(f"Could not read the positions sheet: {exc}")

    summary = price_store.update_prices(sorted(symbols), force=args.force)
    print("Update summary:", summary)
    print("Store status:", price_store.get_status(sorted(symbols)))
    fetched = summary["new"] + summary["incremental"] + summary["resynced"] + summary["up_to_date"]
    return 0 if fetched > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
