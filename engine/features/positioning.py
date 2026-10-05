"""Positioning (COT managed-money) features, point-in-time by publication.

Input: ``md.cot`` indexed by release timestamp (UTC; CFTC/ICE publish Friday 15:30 ET with Tuesday data), with
columns ``period`` (the Tuesday), ``market`` in {brent, wti, gasoil} and ``mm_net`` (managed money net contracts).

Point-in-time rules: per market the table is reduced to one row per period (first-published vintage, see
:func:`engine.features.events.release_table`); trailing statistics are computed release by release; the result
is joined to trading dates with ``merge_asof`` on ``published_at <= settlement_ts(date)``. The Friday 15:30 ET
release falls after the ICE settlement (19:30 London), so a COT report is first visible on the following Monday.

Columns
-------
``COT_MM_NET_BRENT``        latest known ICE Brent managed-money net position (contracts).
``COT_MM_NET_BRENT_PCTL``   percentile rank of the current net position within the trailing ``PCTL_WINDOW``
    releases (3 years of weekly data): share of earlier values strictly below the current one, in [0, 1].
``COT_MM_NET_WTI_PCTL``     same for NYMEX/ICE WTI.
``COT_MM_NET_CHG_4W``       change of the net position over 4 releases.
``COT_CROWDING``            |pctl - 0.5| * 2 in [0, 1].

``COT_MM_NET_CHG_4W`` and ``COT_CROWDING`` use Brent where it is known and fall back to WTI row by row where
Brent is not (ICE COT history starts in 2011). ``result.attrs["cot_crowding_source"]`` records the primary market
("brent" or "wti") and ``result.attrs["cot_crowding_fallback"]`` the market used where the primary is missing
(``None`` when no fallback was available).
"""

from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd

from engine.data.market_data import MarketData
from engine.features import catalog as cat
from engine.features.events import align_released, release_table, settlement_index

PCTL_WINDOW = 156
PCTL_MIN_RELEASES = 52
CHG_RELEASES = 4
MARKETS: tuple[str, ...] = ("brent", "wti")

COLUMNS: list[str] = [
    cat.COT_MM_NET_BRENT,
    cat.COT_MM_NET_BRENT_PCTL,
    cat.COT_MM_NET_WTI_PCTL,
    cat.COT_MM_NET_CHG_4W,
    cat.COT_CROWDING,
]


def trailing_percentile(
    series: pd.Series, window: int = PCTL_WINDOW, min_periods: int = PCTL_MIN_RELEASES
) -> pd.Series:
    """Share of the earlier values in the trailing window that are strictly below the current value."""

    def rank(x: np.ndarray) -> float:
        cur = x[-1]
        prev = x[:-1]
        prev = prev[np.isfinite(prev)]
        if not np.isfinite(cur) or len(prev) < min_periods - 1:
            return np.nan
        return float((prev < cur).mean())

    return series.rolling(window, min_periods=min_periods).apply(rank, raw=True)


def _market_table(cot: pd.DataFrame, market: str) -> pd.DataFrame:
    sub = cot.loc[(cot["market"].astype(str) == market).to_numpy()] if "market" in cot.columns else pd.DataFrame()
    rel = release_table(sub, ["mm_net"])
    if rel.empty:
        rel["pctl"] = pd.Series(dtype=float)
        rel["chg"] = pd.Series(dtype=float)
        return rel
    rel["pctl"] = trailing_percentile(rel["mm_net"])
    rel["chg"] = rel["mm_net"] - rel["mm_net"].shift(CHG_RELEASES)
    return rel


def compute(md: MarketData, asof: datetime, base: pd.DataFrame) -> pd.DataFrame:
    """Positioning features for every trading date of ``base`` (same index, catalog column names only)."""
    out = pd.DataFrame(index=base.index)
    for c in COLUMNS:
        out[c] = np.nan
    out.attrs["cot_crowding_source"] = None
    out.attrs["cot_crowding_fallback"] = None
    if len(base.index) == 0:
        return out
    cutoffs = settlement_index(base.index)

    daily: dict[str, pd.DataFrame] = {}
    for mkt in MARKETS:
        rel = _market_table(md.cot, mkt)
        daily[mkt] = align_released(rel[["mm_net", "pctl", "chg"]], rel["published_at"], cutoffs, base.index)

    brent, wti = daily["brent"], daily["wti"]
    out[cat.COT_MM_NET_BRENT] = brent["mm_net"].astype(float)
    out[cat.COT_MM_NET_BRENT_PCTL] = brent["pctl"].astype(float)
    out[cat.COT_MM_NET_WTI_PCTL] = wti["pctl"].astype(float)

    has_brent = brent["mm_net"].notna().any()
    has_wti = wti["mm_net"].notna().any()
    primary, fallback = ("brent", "wti" if has_wti else None) if has_brent else ("wti", None)
    p, f = daily[primary], (daily[fallback] if fallback else None)
    chg = p["chg"].astype(float)
    pctl = p["pctl"].astype(float)
    if f is not None:
        chg = chg.where(chg.notna(), f["chg"].astype(float))
        pctl = pctl.where(pctl.notna(), f["pctl"].astype(float))
    out[cat.COT_MM_NET_CHG_4W] = chg
    out[cat.COT_CROWDING] = ((pctl - 0.5).abs() * 2.0).clip(0.0, 1.0)
    out.attrs["cot_crowding_source"] = primary if (has_brent or has_wti) else None
    out.attrs["cot_crowding_fallback"] = fallback
    return out
