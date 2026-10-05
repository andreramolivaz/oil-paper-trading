"""Shared numerical helpers for strategies: z-scores, volatility scaling, probability mapping, OU half-life.

Keep these pure and deterministic; strategies import from here instead of re-implementing.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
from scipy.stats import norm

TRADING_DAYS = 252


def zscore(series: pd.Series, window: int, min_periods: int | None = None) -> pd.Series:
    """Rolling z-score of the last value vs the trailing window (excludes nothing; caller handles PIT)."""
    mp = min_periods or max(10, window // 2)
    mu = series.rolling(window, min_periods=mp).mean()
    sd = series.rolling(window, min_periods=mp).std()
    return (series - mu) / sd.replace(0.0, np.nan)


def robust_zscore(series: pd.Series, window: int) -> pd.Series:
    med = series.rolling(window, min_periods=max(10, window // 2)).median()
    mad = (series - med).abs().rolling(window, min_periods=max(10, window // 2)).median()
    return (series - med) / (1.4826 * mad.replace(0.0, np.nan))


def pctl_rank(series: pd.Series, window: int) -> pd.Series:
    """Percentile rank in [0,1] of the latest value within the trailing window (inclusive)."""
    return series.rolling(window, min_periods=max(20, window // 4)).apply(
        lambda x: float((x[:-1] < x[-1]).mean()) if len(x) > 1 else np.nan, raw=True
    )


def vol_scale(target_vol_annual: float, realized_vol_annual: float, cap: float = 1.0, floor_vol: float = 0.08) -> float:
    """Position multiplier so that the position's vol ≈ target; capped at `cap` (never levered here)."""
    rv = max(realized_vol_annual, floor_vol)
    return float(min(cap, target_vol_annual / rv))


def prob_from_z(z: float, scale: float = 1.0, cap: float = 0.80) -> float:
    """Map a signal z-score to a conservative probability of profit: 0.5 + ... bounded to [1-cap, cap]."""
    p = float(norm.cdf(abs(z) / scale))
    p = 0.5 + 0.5 * (p - 0.5) * 2 * 0.6  # shrink toward 0.5: we distrust in-sample sharpness
    return float(min(cap, max(0.5, p)))


def expected_move(vol_annual: float, horizon_days: int) -> float:
    """1-sigma expected absolute move over the horizon as a fraction of price."""
    return float(vol_annual * math.sqrt(max(1, horizon_days) / TRADING_DAYS))


def ou_half_life(series: pd.Series, min_obs: int = 60) -> float | None:
    """Half-life of mean reversion from an AR(1) fit on the series levels (OU discretisation).

    Returns None when the series is not mean reverting (phi >= 1) or too short.
    """
    s = series.dropna()
    if len(s) < min_obs:
        return None
    x = s.shift(1).dropna()
    y = s.loc[x.index]
    x_c = x - x.mean()
    denom = float((x_c**2).sum())
    if denom <= 0:
        return None
    phi = float(((y - y.mean()) * x_c).sum() / denom)
    if phi >= 1.0 or phi <= 0.0:
        return None
    return float(-math.log(2) / math.log(phi))


def clamp(x: float, lo: float, hi: float) -> float:
    return float(max(lo, min(hi, x)))


def sign(x: float) -> int:
    return (x > 0) - (x < 0)


def fmt_pct(x: float, digits: int = 1) -> str:
    """Italian-style percentage for rationales: 0.0123 -> '1,2%'."""
    return f"{x * 100:.{digits}f}%".replace(".", ",")


def fmt_num(x: float, digits: int = 2) -> str:
    return f"{x:.{digits}f}".replace(".", ",")
