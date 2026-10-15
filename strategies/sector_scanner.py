"""
Sector scanner: pull every NSE-listed stock in a sector, run the same technical
checks used on the Position Guide (RS vs Nifty, stabilisation, accumulation,
volatility, volume-profile zone, overhead supply, Buy Score) and turn them into
a plain recommendation per stock.

Sectors are added to SECTORS one at a time; the universe is read from NSE's
official equity list (matched on company name) and cached on disk so it is not
re-downloaded on every run.
"""

from __future__ import annotations

import io
import json
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import requests

from strategies import position_guide as pg

EQUITY_LIST_URL = "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv"
CACHE_PATH = Path(__file__).resolve().parent.parent / "sector_universe_cache.json"

# One entry per sector, added as each is "frozen". A sector is defined either by
# `name_regex` (matched against the company name in NSE's equity list) or by an explicit
# `symbols` list (when the name can't tell, e.g. public vs private banks). `fallback` is
# used only if NSE can't be reached and nothing is cached yet.
SECTORS: dict[str, dict] = {
    "Small Finance Banks": {
        "name_regex": r"small finance bank",
        "fallback": ["AUBANK", "CAPITALSFB", "EQUITASBNK", "ESAFSFB", "JSFB", "SURYODAY", "UJJIVANSFB", "UTKARSHBNK"],
        "note": "Listed small finance banks (NSE equity list, matched on 'Small Finance Bank' in the company name).",
    },
    "Public Sector Banks": {
        "symbols": ["SBIN", "BANKBARODA", "BANKINDIA", "CANBK", "CENTRALBK", "INDIANB", "IOB", "MAHABANK",
                    "PNB", "PSB", "UCOBANK", "UNIONBANK"],
        "note": "The 12 government-owned public sector banks listed on NSE (explicit list).",
    },
    "Private Banks": {
        "symbols": ["HDFCBANK", "ICICIBANK", "AXISBANK", "KOTAKBANK", "INDUSINDBK", "FEDERALBNK", "IDFCFIRSTB",
                    "BANDHANBNK", "RBLBANK", "YESBANK", "IDBI", "CUB", "KARURVYSYA", "SOUTHBANK", "KTKBANK",
                    "DCBBANK", "CSBBANK", "DHANBANK", "J&KBANK", "TMB"],
        "note": "Private sector banks listed on NSE (explicit list, RBI categories). IDBI Bank is included "
                "because RBI classes it as private even though the government and LIC still control it; "
                "J&K Bank is included on the same basis. Small finance and payments banks are excluded.",
    },
}

MIN_LIQUIDITY_CR = 0.5  # avg daily traded value below this (Rs crore) = thin, hard to build a position


@dataclass
class ScanResult:
    ok: bool
    message: str = ""
    table: pd.DataFrame = field(default_factory=pd.DataFrame)
    summary: dict = field(default_factory=dict)
    universe_source: str = ""


def _download_equity_list() -> pd.DataFrame:
    resp = requests.get(EQUITY_LIST_URL, headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
    resp.raise_for_status()
    df = pd.read_csv(io.StringIO(resp.text))
    df.columns = [c.strip() for c in df.columns]
    return df[["SYMBOL", "NAME OF COMPANY", "SERIES", "DATE OF LISTING"]].rename(
        columns={"SYMBOL": "Stock", "NAME OF COMPANY": "Company", "SERIES": "Series", "DATE OF LISTING": "Listed"}
    )


def _load_equity_list(refresh: bool = False) -> tuple[pd.DataFrame | None, str]:
    """NSE equity list, from the on-disk cache unless `refresh` (or no cache yet)."""
    if CACHE_PATH.exists() and not refresh:
        try:
            blob = json.loads(CACHE_PATH.read_text())
            return pd.DataFrame(blob["rows"], columns=blob["columns"]), f"NSE list cached {blob['fetched']}"
        except Exception:
            pass
    try:
        df = _download_equity_list()
    except Exception:
        return None, "NSE list unavailable"
    try:
        CACHE_PATH.write_text(json.dumps({
            "fetched": date.today().isoformat(), "columns": list(df.columns), "rows": df.values.tolist(),
        }))
    except OSError:  # read-only filesystem on some hosts - just skip caching
        pass
    return df, f"NSE list fetched {date.today().isoformat()}"


def load_universe(sector: str, refresh: bool = False) -> tuple[pd.DataFrame, str]:
    cfg = SECTORS[sector]
    explicit = cfg.get("symbols") or cfg.get("fallback")
    equities, source = _load_equity_list(refresh)
    if equities is None:
        return pd.DataFrame({"Stock": explicit, "Company": "", "Series": "", "Listed": ""}), source + " - using built-in list"
    if "symbols" in cfg:
        uni = equities[equities["Stock"].isin(cfg["symbols"])].sort_values("Stock").reset_index(drop=True)
        missing = sorted(set(cfg["symbols"]) - set(uni["Stock"]))
        if missing:
            source += f" - not on NSE list any more: {', '.join(missing)}"
        return uni, source
    mask = equities["Company"].str.contains(cfg["name_regex"], flags=re.I, regex=True)
    uni = equities[mask].sort_values("Stock").reset_index(drop=True)
    return uni, source


def recommend(row: pd.Series) -> tuple[str, str]:
    """Plain-language call from the technicals. Mirrors the base-building framework:
    cheap -> stopped falling -> being accumulated -> clear path up."""
    score = row.get("Buy Score")
    if pd.isna(score) or pd.isna(row.get("52W High Drop")):
        return "Insufficient data", "Not enough price history on Yahoo."

    drop, dsl = row["52W High Drop"], row.get("Days Since Low")
    ud, rs3, sup = row.get("Up/Down Vol"), row.get("RS 3M"), row.get("Supply to +25%")
    liq = row.get("Avg Value 20D")
    good, bad = [], []

    discount = drop <= -0.20
    stabilised = pd.notna(dsl) and dsl >= 20
    falling = pd.notna(dsl) and dsl < 10
    if pd.notna(dsl):
        if stabilised:
            good.append(f"no new low for {dsl:.0f} sessions")
        elif falling:
            bad.append(f"fresh 52W low {dsl:.0f} sessions ago")
    if pd.notna(ud):
        if ud >= 1.2:
            good.append(f"buyers dominate volume ({ud:.1f}x up/down)")
        elif ud < 0.8:
            bad.append(f"sellers dominate volume ({ud:.1f}x up/down)")
    if pd.notna(rs3):
        if rs3 > 0.02:
            good.append(f"beating Nifty by {rs3:.0%} (3M)")
        elif rs3 < -0.02:
            bad.append(f"lagging Nifty by {abs(rs3):.0%} (3M)")
    if pd.notna(sup):
        if sup <= 0.40:
            good.append(f"clear path to +25% ({sup:.0%} supply)")
        elif sup >= 0.70:
            bad.append(f"heavy supply before +25% ({sup:.0%})")
    thin = pd.notna(liq) and liq < MIN_LIQUIDITY_CR

    if falling and discount:
        label = "Avoid for now"
    elif score >= 65 and discount and stabilised:
        label = "Accumulate"
    elif score >= 65 and not discount:
        label = "Strong - wait for dip"
    elif score >= 45:
        label = "Watch - base forming"
    else:
        label = "Avoid for now"

    if thin and label == "Accumulate":
        label = "Watch - base forming"
        bad.append(f"thin liquidity (Rs {liq:.2f} cr/day)")
    elif thin:
        bad.append(f"thin liquidity (Rs {liq:.2f} cr/day)")

    why = ("✔ " + "; ".join(good) if good else "") + (" | " if good and bad else "") + ("✖ " + "; ".join(bad) if bad else "")
    return label, why


def scan_sector(sector: str, market_stats: pd.DataFrame, universe: pd.DataFrame, owned: set | None = None) -> ScanResult:
    """Combine the universe with fetched market stats into the ranked recommendation table."""
    if universe.empty:
        return ScanResult(ok=False, message=f"No stocks found for {sector}.")

    ms = market_stats.reindex(universe["Stock"]).copy()
    t = universe.set_index("Stock")[["Company"]].join(ms).reset_index()
    t["CMP"] = t["Last Close"]
    t["vs POC"] = t["CMP"] / t["VP POC"] - 1.0
    t["VP Zone"] = t.apply(pg._vp_zone, axis=1)
    t["Buy Score"] = t.apply(pg.buy_score, axis=1)
    # No history at all -> no score, rather than a misleading 0.
    no_data = t["Last Close"].isna()
    t.loc[no_data, "Buy Score"] = np.nan

    calls = t.apply(recommend, axis=1, result_type="expand")
    t["Recommendation"], t["Why"] = calls[0], calls[1]
    t["Owned"] = t["Stock"].map(lambda s: "✅ Owned" if owned and s in owned else "")

    order = {"Accumulate": 0, "Strong - wait for dip": 1, "Watch - base forming": 2, "Avoid for now": 3, "Insufficient data": 4}
    t["_o"] = t["Recommendation"].map(order)
    t = t.sort_values(["_o", "Buy Score"], ascending=[True, False]).drop(columns="_o").reset_index(drop=True)
    t["Pick"] = ""
    t.loc[t.index[t["Recommendation"] == "Accumulate"][:3], "Pick"] = "⭐ Top pick"

    valid = t[t["Last Close"].notna()]
    summary = {
        "stocks": len(t),
        "with_data": len(valid),
        "accumulate": int((t["Recommendation"] == "Accumulate").sum()),
        "median_rs3m": float(valid["RS 3M"].median()) if len(valid) else np.nan,
        "median_drop": float(valid["52W High Drop"].median()) if len(valid) else np.nan,
        "above_200dma": float((valid["vs 200DMA"] > 0).mean()) if len(valid) else np.nan,
        "median_score": float(valid["Buy Score"].median()) if len(valid) else np.nan,
    }
    return ScanResult(ok=True, message=f"{len(t)} {sector} stocks scanned.", table=t, summary=summary)
