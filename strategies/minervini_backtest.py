"""Backtest of the Trend Template page's own rules over past years of daily prices.

A signal is taken on a day when the stock, using only the data up to that day's close:
- passes all eight Trend Template conditions (RS rating ranked against the stocks that had enough history on that day),
- trades at least MIN_LIQUIDITY_CR crore a day and had no one-day fall of PRICE_BREAK or worse in the last year (the page hides those too),
- has the entry plan (strategies/entry_plan.py, the same code the page uses) say "Broke out" on the FIRST day of the breakout.
The trade is bought at the plan's entry (that day's close), with the plan's chart stop, and sold automatically at the 2R target (entry + 2 x risk).
The market's state on the signal day is stored with every trade so results can be split by it: the Nifty trend (uptrend / mixed / downtrend, same rule as the
page's banner) and market breadth (share of stocks above their 50-day and 200-day averages, same definition as market_trend.breadth).

Exit rules on the sessions after the buy, using each day's open, high and low:
- the day opens at or below the stop: sold at the open (a gap loss, bigger than 1R); opens at or above the target: sold at the open;
- otherwise the low touching the stop sells at the stop and the high touching the target sells at the target;
- a day that touches both is counted as a loss (the order within the day is unknown);
- a trade that has done neither by the end of the data is "Open" and is valued at the last close.
No brokerage, taxes or slippage. Every signal is simulated on its own; a stock that signals again while a trade is open is flagged by the page, which can drop those.
Weaknesses worth knowing: the stock list is today's (stocks that were delisted are missing, which flatters results), the data is Yahoo's, and there are no fundamentals.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from strategies import entry_plan, price_store
from strategies import trend_template as tt
from strategies.position_guide import PERF_SESSIONS

MIN_LIQUIDITY_CR = 0.5  # same as the Sector Scanner's thin-stock limit
PRICE_BREAK = -0.35  # same as the Sector Scanner's demerger/corporate-action guard
TARGET_R = 2.0
MIN_SESSIONS = 252  # a stock needs this many sessions of its own history before it can be judged (12-month return)


def clean(frame: pd.DataFrame) -> pd.DataFrame:
    """Sorted, de-duplicated OHLCV with bad intraday prints blanked (same rule as price_store.load_history)."""
    g = frame[~frame.index.duplicated(keep="last")].sort_index().dropna(subset=["Close"]).copy()
    g.loc[g["High"] > g["Close"] * (1 + price_store.INTRADAY_RANGE_THRESHOLD), "High"] = np.nan
    g.loc[g["Low"] < g["Close"] * (1 - price_store.INTRADAY_RANGE_THRESHOLD), "Low"] = np.nan
    return g


def nifty_states(close: pd.Series) -> pd.Series:
    """'Uptrend' / 'Mixed' / 'Downtrend' / 'Unknown' for every day, by the same rule as market_trend.nifty_trend."""
    c = close.dropna()
    sma50, sma200 = c.rolling(50).mean(), c.rolling(200).mean()
    before = sma200.shift(21)
    state = pd.Series("Mixed", index=c.index)
    state[c < sma200] = "Downtrend"
    state[(c > sma50) & (sma50 > sma200) & (sma200 > before)] = "Uptrend"
    state[before.isna()] = "Unknown"
    return state


def features(h: pd.DataFrame) -> pd.DataFrame:
    """Per-day Trend Template inputs for one stock (each row uses only data up to that day)."""
    c = h["Close"]
    f = pd.DataFrame(index=h.index)
    f["CMP"] = c
    f["SMA 50"], f["SMA 150"], f["SMA 200"] = c.rolling(50).mean(), c.rolling(150).mean(), c.rolling(200).mean()
    f["SMA 200 1M Ago"] = f["SMA 200"].shift(21)
    hi52, lo52 = c.rolling("366D").max(), c.rolling("366D").min()  # the page's "last 365 days", closing prices
    f["52W High Drop"], f["Off 52W Low"] = c / hi52 - 1.0, c / lo52 - 1.0
    for name, n in PERF_SESSIONS.items():
        prev = c.shift(n)
        f[name] = (c / prev - 1.0).where(prev > 0)
    f["Avg Value 20D"] = (c * h["Volume"]).rolling(20).mean() / 1e7
    f["Worst 1D Move"] = c.pct_change().rolling("366D").min()
    f["Sessions"] = np.arange(1, len(h) + 1)
    # the first session of a breakout is a close above every high of the 149 sessions before it (the plan's own test, as a cheap pre-check)
    high = h["High"].where(h["High"].notna(), c)
    f["New high"] = c > high.shift(1).rolling(149).max()
    return f


def daily_breadth(feats: dict) -> pd.DataFrame:
    """Each day: share of stocks above their 50-day and 200-day averages (same definition as market_trend.breadth, today's live figure)."""
    out = {}
    for key, col in (("breadth50", "SMA 50"), ("breadth200", "SMA 200")):
        close = pd.concat({s: f["CMP"] for s, f in feats.items()}, axis=1)
        sma = pd.concat({s: f[col] for s, f in feats.items()}, axis=1)
        out[key] = (close > sma).astype(float).where(sma.notna() & close.notna()).mean(axis=1)
    return pd.DataFrame(out)


def _exit(h: pd.DataFrame, i: int, entry: float, stop: float, target: float) -> dict:
    """Walk the sessions after position `i` until the stop or the target is hit."""
    o = h["Open"].fillna(h["Close"]).to_numpy()
    hi = h["High"].fillna(h["Close"]).to_numpy()
    lo = h["Low"].fillna(h["Close"]).to_numpy()
    cl = h["Close"].to_numpy()
    best, worst = entry, entry
    for j in range(i + 1, len(h)):
        gap = same_day = 0
        price = None
        if o[j] <= stop:
            price, outcome, gap = o[j], "Loss", 1
        elif o[j] >= target:
            price, outcome, gap = o[j], "Win", 1
        else:
            hit_stop, hit_target = lo[j] <= stop, hi[j] >= target
            if hit_stop:
                price, outcome, same_day = stop, "Loss", int(hit_target)
            elif hit_target:
                price, outcome = target, "Win"
        best, worst = max(best, hi[j]), min(worst, lo[j])
        if price is not None:
            return {"exit_date": h.index[j], "exit_price": float(price), "outcome": outcome, "days_held": j - i, "gap_exit": gap, "same_day": same_day,
                    "max_gain": best / entry - 1.0, "max_drop": min(worst, price) / entry - 1.0}
    last = len(h) - 1
    return {"exit_date": h.index[last], "exit_price": float(cl[last]), "outcome": "Open", "days_held": last - i, "gap_exit": 0, "same_day": 0,
            "max_gain": best / entry - 1.0, "max_drop": worst / entry - 1.0}


def run(histories: dict, nifty: pd.DataFrame, sectors: dict | None = None, progress=None) -> pd.DataFrame:
    """Every signal in the data with how it ended. `histories` = {symbol: OHLCV frame}, `nifty` = the Nifty frame, `sectors` = {symbol: sector text}."""
    sectors = sectors or {}
    states = nifty_states(nifty["Close"])
    hs = {s: clean(h) for s, h in histories.items()}
    hs = {s: h for s, h in hs.items() if len(h) >= MIN_SESSIONS + 30}
    feats = {s: features(h) for s, h in hs.items()}

    # RS rating each day: rank of the blended 3/6/9/12-month return among the stocks that have all four returns that day
    score = pd.concat({s: 0.4 * f["Perf 3M"] + 0.2 * f["Perf 6M"] + 0.2 * f["Perf 9M"] + 0.2 * f["Perf 12M"] for s, f in feats.items()}, axis=1)
    rs = (score.rank(axis=1, pct=True) * 98 + 1).round()
    breadth = daily_breadth(feats)

    rows = []
    for k, (sym, f) in enumerate(feats.items()):
        h = hs[sym]
        r = rs[sym].reindex(f.index)
        c = f["CMP"]
        ok = (
            (c > f["SMA 150"]) & (c > f["SMA 200"]) & (f["SMA 150"] > f["SMA 200"]) & (f["SMA 200"] > f["SMA 200 1M Ago"])
            & (f["SMA 50"] > f["SMA 150"]) & (f["SMA 50"] > f["SMA 200"]) & (c > f["SMA 50"])
            & (f["Off 52W Low"] >= tt.DEFAULTS["above_low"]) & (f["52W High Drop"] >= -tt.DEFAULTS["near_high"]) & (r >= tt.DEFAULTS["min_rs"])
            & (f["Avg Value 20D"] >= MIN_LIQUIDITY_CR) & (f["Worst 1D Move"] > PRICE_BREAK) & f["New high"] & (f["Sessions"] > MIN_SESSIONS)
        )
        for i in np.flatnonzero(ok.to_numpy()):
            day = h.index[i]
            state = states.get(day, "Unknown")
            if state == "Unknown":
                continue
            p = entry_plan.plan(h.iloc[: i + 1])
            if p["Status"] != "Broke out" or p.get("Breakout age") != 0:
                continue
            entry, stop = float(p["Entry"]), float(p["Stop"])
            target = round(entry + TARGET_R * (entry - stop), 2)
            res = _exit(h, i, entry, stop, target)
            rows.append({
                "symbol": sym, "sector": sectors.get(sym, ""), "signal_date": day, "nifty_state": state, "breadth50": float(breadth["breadth50"].get(day, np.nan)),
                "breadth200": float(breadth["breadth200"].get(day, np.nan)), "rs_rating": float(r.iloc[i]),
                "pivot": float(p["Pivot"]), "entry": entry, "stop": stop, "target": target, "risk_pct": float(p["Risk %"]), "trade_check": p["Trade check"],
                "base_weeks": float(p["Base weeks"]), "base_depth": float(p["Base depth"]), "contractions": p["Contractions"],
                "shrinking": int(p["Shrinking"]), "tightening": int(p["Tightening"]), "volume_dried_up": int(p["Volume dried up"]),
                "breakout_quality": int(p["Breakout quality"]), "breakout_vol_x": float(p.get("Breakout vol x", np.nan)),
                "avg_value_cr": float(f["Avg Value 20D"].iloc[i]),
                **res,
                "r_multiple": (res["exit_price"] - entry) / (entry - stop), "pct_return": res["exit_price"] / entry - 1.0,
            })
        if progress and (k + 1) % 100 == 0:
            progress(k + 1, len(feats), len(rows))
    out = pd.DataFrame(rows)
    if out.empty:
        return out
    for col in ("signal_date", "exit_date"):
        out[col] = out[col].dt.strftime("%Y-%m-%d")
    return out.sort_values(["signal_date", "symbol"]).reset_index(drop=True)


def non_overlapping(trades: pd.DataFrame) -> pd.DataFrame:
    """Keep a signal only if the same stock has no earlier kept trade still open on that day (open trades never close)."""
    keep, busy_until = [], {}
    for idx, r in trades.sort_values(["symbol", "signal_date"]).iterrows():
        until = busy_until.get(r["symbol"])
        if until is not None and r["signal_date"] <= until:
            continue
        keep.append(idx)
        busy_until[r["symbol"]] = "9999-12-31" if r["outcome"] == "Open" else r["exit_date"]
    return trades.loc[keep].sort_values(["signal_date", "symbol"])
