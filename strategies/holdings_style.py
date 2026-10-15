"""Shared green/amber/red cell styling for the per-stock technical tables."""

import pandas as pd

GOOD = "background-color: #c6efce; color: #006100"
BAD = "background-color: #ffc7ce; color: #9c0006"
MID = "background-color: #ffeb9c; color: #9c6500"


def _rate(value, good, bad, higher_is_better=True):
    """GOOD/BAD/MID style for a number against two cut-offs (blank if missing)."""
    if value is None or pd.isna(value):
        return ""
    if not higher_is_better:
        value, good, bad = -value, -good, -bad
    if value >= good:
        return GOOD
    if value <= bad:
        return BAD
    return MID


def _vp_zone_style(row):
    """Zone is only 'good' when volume confirms it (see the how-to-read box)."""
    zone, ud, vt = row.get("VP Zone", ""), row.get("Up/Down Vol"), row.get("Vol Trend")
    if zone == "Below value area":
        if pd.isna(ud):
            return ""
        return GOOD if ud >= 1.2 else (BAD if ud < 0.8 else MID)
    if zone == "Above value area":
        if pd.isna(vt):
            return ""
        return GOOD if vt >= 1.0 else MID
    return ""


def style_holdings(df: pd.DataFrame):
    """Green = favours the stock, red = works against it, amber = in between."""
    rules = {
        "RS 1M": lambda v: _rate(v, 0.02, -0.02),
        "RS 3M": lambda v: _rate(v, 0.02, -0.02),
        "vs 200DMA": lambda v: _rate(v, 0.0, -0.10),
        "Vol Trend": lambda v: _rate(v, 1.2, 0.7),
        "Up/Down Vol": lambda v: _rate(v, 1.2, 0.8),
        "Overhead Supply": lambda v: _rate(v, 0.30, 0.70, higher_is_better=False),
        "Supply to +25%": lambda v: _rate(v, 0.40, 0.70, higher_is_better=False),
        "Days Since Low": lambda v: _rate(v, 25, 10),
        "Volatility Ratio": lambda v: _rate(v, 0.85, 1.15, higher_is_better=False),
        "Buy Score": lambda v: _rate(v, 65, 40),
    }
    styler = df.style
    for col, fn in rules.items():
        if col in df:
            styler = styler.map(lambda v, fn=fn: fn(v), subset=[col])
    if "VP Zone" in df:
        zone_styles = df.apply(_vp_zone_style, axis=1)
        styler = styler.apply(lambda col: zone_styles, subset=["VP Zone"])
    return styler
