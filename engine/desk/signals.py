"""Forecasts for the desk books. Pure, causal functions of daily series.

Every function returns a series aligned to its input index whose value at ``t`` uses rows up to and including
``t`` only (``ewm``, ``rolling`` and ``shift`` with non-negative lags): truncating the input after ``t`` cannot
change the value at ``t``. ``tests/test_desk_signals.py`` pins that down.

Forecast scale (Carver, *Systematic Trading*): 0 is no view, an average absolute value of 10 is a normal
conviction, 20 is the cap. A forecast of +10 buys the exposure that hits the book's volatility target.

Nothing here was fitted on the data it is tested on: the EWMAC spans and scalars are Carver's published ones,
the 20-day carry-momentum window is Bouchouev's (GCARD 2020), the combination is an equal-weight average.
"""

from __future__ import annotations

import math
from collections.abc import Mapping

import numpy as np
import pandas as pd

TRADING_DAYS = 252
FORECAST_CAP = 20.0
FORECAST_NORMAL = 10.0

# Carver's EWMAC speeds and forecast scalars ("Advanced Futures Trading Strategies", 2023). The two fastest
# pairs (2/8, 4/16) are left out: they turn over several times a month and add little after costs.
EWMAC_PAIRS: tuple[tuple[int, int], ...] = ((8, 32), (16, 64), (32, 128), (64, 256))
EWMAC_SCALARS: dict[tuple[int, int], float] = {(8, 32): 5.95, (16, 64): 4.10, (32, 128): 2.79, (64, 256): 1.91}
TREND_FDM = 1.20  # the four speeds are correlated ~0.7-0.9: their average is smaller than each of them
VOL_SPAN = 35
VOL_FLOOR = 0.10
CARRY_DEADBAND = 0.01  # annualised slope below 1 % is "flat": no carry view
CARRY_MOMENTUM_WINDOW = 20
SLEEVES = ("trend", "carry", "carry_momentum")
COMBINED_FDM = 1.25  # sleeve returns correlate 0.2-0.6 in 1986-2024: 1 / sqrt(w'Cw) for three equal weights


def ew_vol(returns: pd.Series, span: int = VOL_SPAN, floor: float = VOL_FLOOR) -> pd.Series:
    """Annualised exponentially weighted volatility of daily returns, floored (never sized on a tiny vol)."""
    vol = returns.ewm(span=span, min_periods=20).std() * math.sqrt(TRADING_DAYS)
    return vol.clip(lower=floor)


def total_return_index(returns: pd.Series) -> pd.Series:
    """Index of an investable return series (starts at 1). It has no roll jump by construction."""
    return (1.0 + returns.fillna(0.0)).cumprod()


def trend_forecast(returns: pd.Series) -> pd.Series:
    """Average of four EWMAC crossovers on the investable index, each normalised by price volatility."""
    index = total_return_index(returns)
    vol_points = index * returns.ewm(span=VOL_SPAN, min_periods=20).std()
    vol_points = vol_points.where(vol_points > 0)
    parts = []
    for fast, slow in EWMAC_PAIRS:
        raw = (index.ewm(span=fast, min_periods=slow).mean() - index.ewm(span=slow, min_periods=slow).mean()) / (
            vol_points
        )
        parts.append((raw * EWMAC_SCALARS[fast, slow]).clip(-FORECAST_CAP, FORECAST_CAP))
    combined = sum(parts[1:], parts[0]) / len(parts) * TREND_FDM
    return combined.clip(-FORECAST_CAP, FORECAST_CAP)


def carry_forecast(slope: pd.Series, deadband: float = CARRY_DEADBAND) -> pd.Series:
    """+10 in backwardation, -10 in contango, 0 inside the deadband. ``slope`` is the annualised curve slope
    (front minus far over far, per year): positive means the curve pays you to be long."""
    out = pd.Series(0.0, index=slope.index)
    out = out.mask(slope > deadband, FORECAST_NORMAL).mask(slope < -deadband, -FORECAST_NORMAL)
    return out.where(slope.notna())


def carry_momentum_forecast(slope: pd.Series, window: int = CARRY_MOMENTUM_WINDOW) -> pd.Series:
    """+10 when the slope is above its own ``window``-day average (the curve is tightening), -10 below."""
    average = slope.rolling(window, min_periods=window).mean()
    diff = slope - average
    out = pd.Series(np.sign(diff) * FORECAST_NORMAL, index=slope.index)
    return out.where(diff.notna())


def splice(preferred: pd.Series, fallback: pd.Series) -> tuple[pd.Series, pd.Series]:
    """``preferred`` where it has a value, ``fallback`` elsewhere. Also returns the mask of fallback rows.

    Forecasts are spliced, never the underlying levels: a moving average straddling two different slope
    definitions would compare numbers that are not the same thing.
    """
    index = preferred.index.union(fallback.index)
    first = preferred.reindex(index)
    second = fallback.reindex(index)
    use_fallback = first.isna() & second.notna()
    return first.where(first.notna(), second), use_fallback


def combine(
    forecasts: Mapping[str, pd.Series],
    weights: Mapping[str, float] | None = None,
    fdm: float = COMBINED_FDM,
) -> pd.Series:
    """Weighted average of the sleeves times the diversification multiplier, capped at +-20.

    A sleeve with no value on a day (no curve data, warm-up) is left out of that day's average and the remaining
    weights are renormalised; the multiplier is then scaled down, because fewer sleeves diversify less.
    """
    names = [n for n in forecasts if (weights is None or float(weights.get(n, 0.0)) > 0)]
    if not names:
        raise ValueError("no sleeve with a positive weight")
    frame = pd.DataFrame({n: forecasts[n] for n in names})
    w = pd.Series({n: 1.0 if weights is None else float(weights[n]) for n in names})
    present = frame.notna()
    weight_sum = present.mul(w, axis=1).sum(axis=1)
    weighted = frame.fillna(0.0).mul(w, axis=1).sum(axis=1) / weight_sum.where(weight_sum > 0)
    share = present.sum(axis=1) / len(names)
    multiplier = 1.0 + (fdm - 1.0) * ((share * len(names) - 1.0) / max(1, len(names) - 1)).clip(lower=0.0)
    return (weighted * multiplier).clip(-FORECAST_CAP, FORECAST_CAP)


def combine_values(
    values: Mapping[str, float | None],
    weights: Mapping[str, float] | None = None,
    fdm: float = COMBINED_FDM,
) -> float | None:
    """Scalar twin of :func:`combine` for one day: same weights, same renormalisation, same multiplier.

    Returns None when no sleeve with a positive weight has a value.
    """
    names = [n for n in SLEEVES if weights is None or float(weights.get(n, 0.0)) > 0]
    if not names:
        return None
    total = 0.0
    weight_sum = 0.0
    present = 0
    for name in names:
        value = values.get(name)
        if value is None or not math.isfinite(value):
            continue
        w = 1.0 if weights is None else float(weights[name])
        total += w * float(value)
        weight_sum += w
        present += 1
    if present == 0 or weight_sum <= 0:
        return None
    multiplier = 1.0 + (fdm - 1.0) * max(0.0, (present - 1.0) / max(1, len(names) - 1))
    return float(max(-FORECAST_CAP, min(FORECAST_CAP, total / weight_sum * multiplier)))


def exposure_fraction(
    forecast: float,
    vol_annual: float,
    vol_target: float,
    max_leverage: float,
    long_only: bool = False,
) -> float:
    """Signed exposure as a multiple of equity: ``forecast / 10 * vol_target / vol``, capped.

    This is the whole leverage policy of a book. With the forecast at its normal size the position runs at the
    volatility target; a forecast of 20 doubles it; ``max_leverage`` is the ceiling whatever the inputs say.
    """
    if not (math.isfinite(forecast) and math.isfinite(vol_annual)) or vol_annual <= 0:
        return 0.0
    raw = forecast / FORECAST_NORMAL * vol_target / vol_annual
    if long_only:
        raw = max(0.0, raw)
    return float(max(-max_leverage, min(max_leverage, raw)))
