"""One cached scan of every sector from the stored prices (no downloads), shared by the Overview and Screener pages."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from strategies import position_guide as pg
from strategies import sector_scanner as scanner


@st.cache_data(ttl=3600, show_spinner="Scanning all sectors from the stored prices (about 15 seconds the first time)...")
def scan_all_sectors(nonce: int = 0):
    """Return (table, summaries): one row per stock across all sectors (with a Sector column, a stock listed in
    two sectors appears once) and {sector: summary dict} as produced by scan_sector."""
    universes = {name: scanner.load_universe(name)[0] for name in scanner.SECTORS}
    symbols = sorted({s for u in universes.values() for s in u["Stock"]})
    stats = pg.fetch_market_stats(symbols, update=False)
    tables, summaries = [], {}
    for name, uni in universes.items():
        res = scanner.scan_sector(name, stats, uni, owned=set())
        if not res.ok:
            continue
        t = res.table.copy()
        t.insert(0, "Sector", name)
        tables.append(t)
        summaries[name] = res.summary
    full = pd.concat(tables, ignore_index=True)
    return full, summaries
