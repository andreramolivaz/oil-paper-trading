"""News / geopolitics features from the GPR index (Caldara-Iacoviello) and GDELT, point-in-time by publication.

Input: ``md.news`` indexed by calendar date with columns ``gpr``, ``gpr_act``, ``gpr_threat`` (daily GPR, 1985+) and
``gdelt_volume``, ``gdelt_tone`` ... (NaN before the GDELT go-live).

Publication lag
---------------
* When the adapter provides ``md.published_at["news"]`` (one UTC timestamp per row) it is used for every column.
* Otherwise the daily GPR file is assumed to be published **two business days** after the date it refers to
  (``GPR_LAG_BDAYS``, at 00:00 UTC of that day), and a GDELT daily aggregate is complete at 00:00 UTC of the next
  calendar day. Both are joined to trading dates with ``merge_asof`` on ``published_at <= settlement_ts(date)``,
  so the value for Monday is first usable on Wednesday's settlement (GPR) or Tuesday's (GDELT).
* Values are never forward-filled: a missing day in the source stays NaN on the trading dates that would see it.
* The level features are the **latest known row**. GPR is a 7-day series, so the Friday, Saturday and Sunday
  rows all become known on Tuesday and Sunday's row is the level used from then on; a Friday-only or
  Saturday-only spike therefore never enters the trading-date series. Monday's settlement sees Thursday's row.

Columns
-------
``GPR``               latest known GPR level.
``GPR_Z``             trailing 252-day z-score of ``GPR``; ``GPR_THREAT_Z`` the same for the threat sub-index.
``GDELT_TONE``        latest known GDELT average tone (negative = hostile).
``GDELT_VOLUME_Z``    trailing 63-day z-score of GDELT article volume.
``GEO_INDEX``         logistic(blend) in [0, 1] where blend is the weighted mean of the available components
    ``GPR_Z`` (0.5), ``GDELT_VOLUME_Z`` (0.3) and ``-z63(GDELT_TONE)`` (0.2); weights are renormalised over the
    components that are not NaN, so before GDELT the index is logistic(GPR_Z). 0.5 = normal, 0.88 = two sigma.
``GEO_SPIKE``         1.0 when ``GEO_INDEX`` exceeds the mean of its previous 10 values plus 1.5 times their std
    *and* exceeds ``SPIKE_FLOOR`` (0.6); 0.0 otherwise; NaN where ``GEO_INDEX`` is NaN.
``DEESCALATION_FLAG`` 1.0 when GDELT tone rose by more than one trailing (63-day, prior values) std over three
    trading days while ``GDELT_VOLUME_Z`` > 1 (the story is still big but turning benign); 0.0 otherwise.
    Without GDELT every comparison is NaN -> the flag is 0.0 everywhere (documented: "no evidence", not "unknown").
"""

from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd

from engine.data.market_data import MarketData
from engine.features import catalog as cat
from engine.features.events import align_released, rolling_zscore, settlement_index, to_utc_ns, trading_dates

GPR_COLUMNS: tuple[str, ...] = ("gpr", "gpr_act", "gpr_threat")
GDELT_COLUMNS: tuple[str, ...] = ("gdelt_volume", "gdelt_tone", "gdelt_goldstein", "gdelt_conflict_share")
GPR_LAG_BDAYS = 2
GDELT_LAG_DAYS = 1
GPR_Z_WINDOW = 252
GPR_Z_MIN = 126
GDELT_Z_WINDOW = 63
GDELT_Z_MIN = 30
GEO_WEIGHTS: tuple[float, float, float] = (0.5, 0.3, 0.2)  # GPR_Z, GDELT_VOLUME_Z, -tone_z
SPIKE_WINDOW = 10
SPIKE_MIN = 5
SPIKE_SIGMAS = 1.5
SPIKE_FLOOR = 0.6
DEESC_DAYS = 3
DEESC_VOLUME_Z = 1.0

COLUMNS: list[str] = [
    cat.GPR,
    cat.GPR_Z,
    cat.GPR_THREAT_Z,
    cat.GDELT_TONE,
    cat.GDELT_VOLUME_Z,
    cat.GEO_INDEX,
    cat.GEO_SPIKE,
    cat.DEESCALATION_FLAG,
]


def _aligned_news(md: MarketData, cutoffs: pd.DatetimeIndex, index: pd.Index) -> pd.DataFrame:
    """``md.news`` joined to trading dates by publication time (see module docstring for the lag rules)."""
    wanted = [*GPR_COLUMNS, *GDELT_COLUMNS]
    news = md.news
    out = pd.DataFrame(index=index, columns=wanted, dtype=float)
    if news.empty:
        return out
    present = [c for c in wanted if c in news.columns]
    override = md.published_at.get("news")
    if override is not None and len(override):
        pub = pd.Series(override).reindex(news.index)
        aligned = align_released(news[present], pub, cutoffs, index)
        for c in present:
            out[c] = aligned[c].astype(float)
        return out
    dates = trading_dates(news.index)
    groups = (
        ([c for c in GPR_COLUMNS if c in present], to_utc_ns(dates + GPR_LAG_BDAYS * pd.offsets.BDay())),
        ([c for c in GDELT_COLUMNS if c in present], to_utc_ns(dates + pd.Timedelta(days=GDELT_LAG_DAYS))),
    )
    for cols, pub_idx in groups:
        if not cols:
            continue
        aligned = align_released(news[cols], pub_idx, cutoffs, index)
        for c in cols:
            out[c] = aligned[c].astype(float)
    return out


def _logistic_blend(components: list[pd.Series], weights: tuple[float, ...]) -> pd.Series:
    """logistic(weighted mean of the non-NaN components), NaN where none is available."""
    mat = pd.concat(components, axis=1).to_numpy(dtype=float)
    w = np.asarray(weights, dtype=float)
    mask = np.isfinite(mat)
    wsum = (mask * w).sum(axis=1)
    num = (np.where(mask, mat, 0.0) * w).sum(axis=1)
    blend = np.divide(num, wsum, out=np.full(len(wsum), np.nan), where=wsum > 0)
    return pd.Series(1.0 / (1.0 + np.exp(-blend)), index=components[0].index)


def compute(md: MarketData, asof: datetime, base: pd.DataFrame) -> pd.DataFrame:
    """News features for every trading date of ``base`` (same index, catalog column names only)."""
    out = pd.DataFrame(index=base.index)
    for c in COLUMNS:
        out[c] = np.nan
    if len(base.index) == 0:
        return out
    news = _aligned_news(md, settlement_index(base.index), base.index)

    gpr = news["gpr"]
    tone = news["gdelt_tone"]
    volume = news["gdelt_volume"]
    gpr_z = rolling_zscore(gpr, GPR_Z_WINDOW, GPR_Z_MIN)
    volume_z = rolling_zscore(volume, GDELT_Z_WINDOW, GDELT_Z_MIN)
    tone_z = rolling_zscore(tone, GDELT_Z_WINDOW, GDELT_Z_MIN)

    out[cat.GPR] = gpr
    out[cat.GPR_Z] = gpr_z
    out[cat.GPR_THREAT_Z] = rolling_zscore(news["gpr_threat"], GPR_Z_WINDOW, GPR_Z_MIN)
    out[cat.GDELT_TONE] = tone
    out[cat.GDELT_VOLUME_Z] = volume_z

    geo = _logistic_blend([gpr_z, volume_z, -tone_z], GEO_WEIGHTS)
    out[cat.GEO_INDEX] = geo
    prior = geo.shift(1)
    mu = prior.rolling(SPIKE_WINDOW, min_periods=SPIKE_MIN).mean()
    sd = prior.rolling(SPIKE_WINDOW, min_periods=SPIKE_MIN).std()
    spike = (geo > mu + SPIKE_SIGMAS * sd) & (geo > SPIKE_FLOOR)
    out[cat.GEO_SPIKE] = spike.astype(float).where(geo.notna())

    tone_rise = tone - tone.shift(DEESC_DAYS)
    tone_sd = tone.shift(1).rolling(GDELT_Z_WINDOW, min_periods=GDELT_Z_MIN).std()
    deesc = (tone_rise > tone_sd) & (volume_z > DEESC_VOLUME_Z)
    out[cat.DEESCALATION_FLAG] = deesc.astype(float)  # NaN comparisons are False -> 0.0 when GDELT is absent
    return out
