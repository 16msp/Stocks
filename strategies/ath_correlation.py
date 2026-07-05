"""
Correlation-based basket segregation for the ATH-drop averaging universe.

Many ETFs in the tracked universe move together (overlapping index
constituents, same broad market exposure) - trading all of them isn't
diversification, it's the same bet repeated, which is exactly why entries
cluster and capital demand spikes during market-wide moves (see Cash Flow
Planner). This groups ETFs into correlation-based "baskets" (hierarchical
clustering on daily return correlation), picks one representative per basket
(highest liquidity), and produces a "diversified" universe - one bet per
basket instead of several redundant ones - for use elsewhere (optimizer,
cash-flow planner) to compare against the undifferentiated full universe.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from scipy.cluster.hierarchy import fcluster, linkage
from scipy.spatial.distance import squareform

from strategies import ath_averaging as ath
from strategies import nse_etf_momentum as base

MIN_OVERLAP_DAYS = 250  # need at least ~1yr of overlapping trading days to trust a correlation estimate
DEFAULT_DISTANCE_THRESHOLD = 0.3  # clusters merge while (1 - correlation) <= this, i.e. correlation >= 0.7
LINKAGE_METHOD = "complete"  # requires ALL cross-cluster pairs to satisfy the threshold, not just one chain of
# loosely-linked pairs ("average"/"single" linkage let one weak chain drag unrelated ETFs into one giant blob -
# tested on this universe: average-linkage at a 0.6-correlation cutoff merged 66 of 90 ETFs into one cluster)


def compute_return_matrix(symbols: list[str], prices: pd.DataFrame | None = None) -> pd.DataFrame:
    """Wide date x symbol matrix of daily log returns, for correlation computation."""
    if prices is None:
        prices = base.read_daily_prices()
        prices, _bad = base.drop_bad_ticks(prices)
    sub = prices[prices["symbol"].isin(symbols)][["date", "symbol", "close"]]
    wide = sub.pivot(index="date", columns="symbol", values="close").sort_index()
    return np.log(wide / wide.shift(1))


def compute_correlation_matrix(symbols: list[str], min_overlap_days: int = MIN_OVERLAP_DAYS) -> pd.DataFrame:
    returns = compute_return_matrix(symbols)
    return returns.corr(min_periods=min_overlap_days)


@dataclass
class ClusterResult:
    ok: bool
    message: str = ""
    corr_matrix: pd.DataFrame = field(default_factory=pd.DataFrame)
    assignments: pd.DataFrame = field(default_factory=pd.DataFrame)  # symbol, description, cluster_id, avg_volume, is_representative
    n_clusters: int = 0


def cluster_etfs(
    symbols: list[str] | None = None,
    distance_threshold: float = DEFAULT_DISTANCE_THRESHOLD,
    min_overlap_days: int = MIN_OVERLAP_DAYS,
) -> ClusterResult:
    """distance_threshold: lower = stricter (fewer, tighter baskets); higher = looser (more gets merged)."""
    if symbols is None:
        symbols = ath.get_universe_symbol_list()

    corr = compute_correlation_matrix(symbols, min_overlap_days=min_overlap_days)
    valid = corr.dropna(how="all").index  # drop symbols with no usable overlap against anything
    corr = corr.loc[valid, valid]
    if len(corr) < 2:
        return ClusterResult(ok=False, message="Not enough overlapping history across symbols to compute correlations.")

    corr_filled = corr.fillna(0.0)  # a specific pair lacking enough overlap -> treat as uncorrelated, not merged
    dist = 1 - corr_filled.clip(-1, 1)
    dist_vals = dist.to_numpy()
    np.fill_diagonal(dist_vals, 0.0)
    dist_vals = (dist_vals + dist_vals.T) / 2  # force exact symmetry (float rounding can break it slightly)
    condensed = squareform(dist_vals, checks=False)
    z = linkage(condensed, method=LINKAGE_METHOD)
    cluster_ids = fcluster(z, t=distance_threshold, criterion="distance")

    meta = base.read_meta().set_index("symbol")["category"].to_dict()
    vol_map = base.get_representative_symbols().set_index("symbol")["avg_volume"].to_dict()

    assignments = pd.DataFrame({
        "symbol": corr.index,
        "description": [meta.get(s, "") for s in corr.index],
        "cluster_id": cluster_ids,
        "avg_volume": [vol_map.get(s, 0.0) for s in corr.index],
    })
    assignments = assignments.sort_values("avg_volume", ascending=False)
    assignments["is_representative"] = ~assignments.duplicated("cluster_id", keep="first")
    assignments = assignments.sort_values(["cluster_id", "avg_volume"], ascending=[True, False]).reset_index(drop=True)

    n_clusters = int(assignments["cluster_id"].nunique())
    return ClusterResult(
        ok=True,
        message=f"{len(assignments)} ETFs grouped into {n_clusters} correlation baskets "
        f"(distance threshold {distance_threshold}, i.e. correlation >= {1 - distance_threshold:.2f} to merge).",
        corr_matrix=corr, assignments=assignments, n_clusters=n_clusters,
    )


def get_diversified_symbol_list(cluster_result: ClusterResult) -> list[str]:
    return cluster_result.assignments[cluster_result.assignments["is_representative"]]["symbol"].tolist()
