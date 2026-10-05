"""Inter-market features: Brent-WTI, crack spreads, product lead. All prices in USD/bbl.

Inputs: ``base[PX_FRONT]`` (Brent front settlement) and ``md.prices`` columns ``wti_front_close`` (USD/bbl),
``rbob_close`` and ``ho_close`` (USD/gal, converted with 42 gal/bbl). Daily closes of trading date t settle at or
before the ICE settlement of t, so they are aligned by date (``reindex``, never forward-filled: a missing close
leaves the feature NaN).

Columns
-------
``BRENT_WTI``       PX_FRONT - wti_front_close.
``BRENT_WTI_Z``     trailing 252-day z-score of the residual of a causal OLS ``brent = a + b * wti``:
    the coefficients are re-estimated on the first trading day of every month from the ``OLS_WINDOW`` (756) rows
    strictly before that day (at least ``OLS_MIN_OBS`` valid pairs, else NaN) and then held fixed for the month, so
    every residual uses only coefficients known before the day it is evaluated on. Spec: S8 "regressione rolling
    3 anni" (the exogenous drivers cushing_vs_5y / geo_index / crude_exports are left to the strategy layer).
``CRACK_321``       (2 * rbob * 42 + ho * 42 - 3 * wti) / 3.
``CRACK_321_Z``     trailing 252-day z-score of ``CRACK_321``.
``DIESEL_CRACK``    ho * 42 - PX_FRONT;  ``GASOLINE_CRACK``  rbob * 42 - PX_FRONT.
``PRODUCT_LEAD``    5-day log return of the product basket (2/3 rbob + 1/3 ho, per bbl) minus the 5-day log
    return of WTI (products leading crude).
"""

from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd

from engine.data.market_data import MarketData
from engine.features import catalog as cat
from engine.features.events import rolling_zscore, trading_dates

GAL_PER_BBL = 42.0
OLS_WINDOW = 756
OLS_MIN_OBS = 252
Z_WINDOW = 252
Z_MIN = 126
LEAD_DAYS = 5

COLUMNS: list[str] = [
    cat.BRENT_WTI,
    cat.BRENT_WTI_Z,
    cat.CRACK_321,
    cat.CRACK_321_Z,
    cat.DIESEL_CRACK,
    cat.GASOLINE_CRACK,
    cat.PRODUCT_LEAD,
]


def _price(md: MarketData, column: str, index: pd.Index) -> pd.Series:
    """``md.prices[column]`` aligned to the trading dates of ``index`` by date (no fill)."""
    if md.prices.empty or column not in md.prices.columns:
        return pd.Series(np.nan, index=index, dtype=float)
    s = pd.Series(
        pd.to_numeric(md.prices[column], errors="coerce").to_numpy(dtype=float), index=trading_dates(md.prices.index)
    )
    s = s[~s.index.duplicated(keep="last")]
    return pd.Series(s.reindex(trading_dates(index)).to_numpy(), index=index, dtype=float)


def causal_ols_residual(y: pd.Series, x: pd.Series, window: int = OLS_WINDOW, min_obs: int = OLS_MIN_OBS) -> pd.Series:
    """Residual of ``y = a + b * x`` with coefficients refit on the first row of each calendar month using the
    ``window`` rows strictly before it; NaN until the first successful fit."""
    n = len(y)
    yv = y.to_numpy(dtype=float)
    xv = x.to_numpy(dtype=float)
    days = trading_dates(y.index)
    month_key = days.year.to_numpy() * 12 + days.month.to_numpy()
    is_refit = np.ones(n, dtype=bool)
    is_refit[1:] = month_key[1:] != month_key[:-1]
    a = np.full(n, np.nan)
    b = np.full(n, np.nan)
    cur_a = cur_b = np.nan
    for i in range(n):
        if is_refit[i]:
            lo = max(0, i - window)
            ys, xs = yv[lo:i], xv[lo:i]
            ok = np.isfinite(ys) & np.isfinite(xs)
            if ok.sum() >= min_obs and np.var(xs[ok]) > 0:
                cur_b, cur_a = np.polyfit(xs[ok], ys[ok], 1)
            else:
                cur_a = cur_b = np.nan
        a[i], b[i] = cur_a, cur_b
    return pd.Series(yv - (a + b * xv), index=y.index)


def compute(md: MarketData, asof: datetime, base: pd.DataFrame) -> pd.DataFrame:
    """Inter-market features for every trading date of ``base`` (same index, catalog column names only)."""
    out = pd.DataFrame(index=base.index)
    for c in COLUMNS:
        out[c] = np.nan
    if len(base.index) == 0:
        return out
    brent = (
        pd.to_numeric(base[cat.PX_FRONT], errors="coerce").astype(float)
        if cat.PX_FRONT in base.columns
        else pd.Series(np.nan, index=base.index)
    )
    wti = _price(md, "wti_front_close", base.index)
    rbob = _price(md, "rbob_close", base.index) * GAL_PER_BBL
    ho = _price(md, "ho_close", base.index) * GAL_PER_BBL

    out[cat.BRENT_WTI] = brent - wti
    out[cat.BRENT_WTI_Z] = rolling_zscore(causal_ols_residual(brent, wti), Z_WINDOW, Z_MIN)
    crack = (2.0 * rbob + ho - 3.0 * wti) / 3.0
    out[cat.CRACK_321] = crack
    out[cat.CRACK_321_Z] = rolling_zscore(crack, Z_WINDOW, Z_MIN)
    out[cat.DIESEL_CRACK] = ho - brent
    out[cat.GASOLINE_CRACK] = rbob - brent
    basket = (2.0 * rbob + ho) / 3.0
    with np.errstate(divide="ignore", invalid="ignore"):
        lead = np.log(basket / basket.shift(LEAD_DAYS)) - np.log(wti / wti.shift(LEAD_DAYS))
    out[cat.PRODUCT_LEAD] = lead
    return out
