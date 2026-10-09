"""Forecasts for the desk books. Pure, causal functions of daily series.

Every function returns a series aligned to its input index whose value at ``t`` uses rows up to and including
``t`` only (``ewm``, ``rolling``, ``expanding`` and ``shift`` with non-negative lags): truncating the input
after ``t`` cannot change the value at ``t``. ``tests/test_desk_signals.py`` pins that down.

Forecast scale (Carver, *Systematic Trading*): 0 is no view, an average absolute value of 10 is a normal
conviction, 20 is the cap. A forecast of +10 buys the exposure that hits the book's volatility target.

Seven sleeves, grouped by where their information comes from, each source with a third of the weight:

* **the price of the market itself**: trend (EWMAC), acceleration (the change of the trend), skew (a market
  whose returns have been more negatively skewed than usual pays a premium);
* **the futures curve**: carry (backwardation or contango) and carry momentum (is the curve tightening);
* **other markets**: the trend of copper and of the dollar (inverted), read on yesterday's close. Oil follows
  the global cycle with a lag, and those two markets carry it with fewer supply shocks of their own.

Nothing here was fitted on the data it is tested on. The EWMAC, acceleration and skew rules, their spans and
their forecast scalars are the ones published with pysystemtrade (``systems/provided/rob_system/config.yaml``),
the 20-day carry-momentum window is Bouchouev's (GCARD 2020), the macro sleeves are the desk's own trend rule
applied unchanged to another market, the weights are equal by source and the diversification multipliers come
from Carver's formula ``1 / sqrt(w' C w)`` on the measured forecast correlations. What was tried and left out
is in ``docs/RESEARCH.md`` with its numbers.
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
# Acceleration: EWMAC(n, 4n) minus its own value n days earlier (pysystemtrade accel16/32/64 and scalars).
ACCEL_SCALARS: dict[int, float] = {16: 7.82, 32: 5.56, 64: 3.90}
ACCEL_FDM = 1.38  # the three speeds correlate 0.47, 0.41 and about zero on WTI 1986-2026
# Skew: minus the rolling skewness of daily returns, against its own history, in units of its own volatility,
# smoothed (pysystemtrade skewabs180/365: lookback -> (smoothing span, forecast scalar)).
SKEW_RULES: dict[int, tuple[int, float]] = {180: (45, 4.59), 365: (90, 2.35)}
SKEW_FDM = 1.11  # the two lookbacks correlate 0.63
SKEW_MIN_HISTORY = 500  # days of skew needed before "its own history" means anything
VOL_SPAN = 35
VOL_FLOOR = 0.10
CARRY_DEADBAND = 0.01  # annualised slope below 1 % is "flat": no carry view
CARRY_MOMENTUM_WINDOW = 20

# Where the information of each sleeve comes from. The sources weigh the same; so do the sleeves inside one.
SOURCES: dict[str, tuple[str, ...]] = {
    "prezzo": ("trend", "accel", "skew"),
    "curva": ("carry", "carry_momentum"),
    "macro": ("copper", "dollar"),
}
SLEEVES: tuple[str, ...] = tuple(name for members in SOURCES.values() for name in members)
SOURCE_OF: dict[str, str] = {name: source for source, members in SOURCES.items() for name in members}
DEFAULT_WEIGHTS: dict[str, float] = {
    name: 1.0 / len(SOURCES) / len(members) for members in SOURCES.values() for name in members
}
# 1 / sqrt(w' C w) with the weights above and the forecast correlations measured on WTI since 2001, the first
# year with all seven sleeves (negative correlations counted as zero): 1.77; on the Brent fund 1.73.
COMBINED_FDM = 1.75


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


def accel_forecast(returns: pd.Series) -> pd.Series:
    """Acceleration: is the trend getting stronger or weaker. Each speed is EWMAC(n, 4n) minus its own value
    ``n`` days earlier; a trend that is still up but fading reads negative."""
    index = total_return_index(returns)
    vol_points = index * returns.ewm(span=VOL_SPAN, min_periods=20).std()
    vol_points = vol_points.where(vol_points > 0)
    parts = []
    for fast, scalar in ACCEL_SCALARS.items():
        slow = 4 * fast
        ewmac = (index.ewm(span=fast, min_periods=slow).mean() - index.ewm(span=slow, min_periods=slow).mean()) / (
            vol_points
        )
        parts.append(((ewmac - ewmac.shift(fast)) * scalar).clip(-FORECAST_CAP, FORECAST_CAP))
    combined = sum(parts[1:], parts[0]) / len(parts) * ACCEL_FDM
    return combined.clip(-FORECAST_CAP, FORECAST_CAP)


def _robust_vol(values: pd.Series) -> pd.Series:
    """pysystemtrade's ``robust_vol_calc``: an exponentially weighted standard deviation that is never allowed
    below the 5 % quantile of its own last 500 values (a factor that has stopped moving is not a strong one)."""
    vol = values.ewm(adjust=True, span=VOL_SPAN, min_periods=10).std().clip(lower=1e-10)
    floor = vol.rolling(500, min_periods=100).quantile(0.05)
    return vol.where(floor.isna() | (vol >= floor), floor)


def skew_forecast(returns: pd.Series) -> pd.Series:
    """Skew: long when the returns of the market have been more negatively skewed than in its own past.

    Negative skew is what investors dislike (rare large losses), so a market that shows more of it than usual
    has to pay for being held. pysystemtrade demeans the factor by its average over all its instruments; one
    market has only its own history, which is why this is computed on the longest series there is (the WTI
    future since 1985) and that one reading is used for the Brent fund as well.
    """
    parts = []
    for lookback, (smooth, scalar) in SKEW_RULES.items():
        negative_skew = -returns.rolling(lookback).skew()
        demeaned = negative_skew - negative_skew.expanding(min_periods=SKEW_MIN_HISTORY).mean()
        normalised = demeaned / _robust_vol(demeaned)
        parts.append((normalised.ewm(span=smooth).mean() * scalar).clip(-FORECAST_CAP, FORECAST_CAP))
    combined = sum(parts[1:], parts[0]) / len(parts) * SKEW_FDM
    return combined.clip(-FORECAST_CAP, FORECAST_CAP)


def macro_trend_forecast(close: pd.Series, inverse: bool = False) -> pd.Series:
    """The desk's own trend rule read on the daily closes of ANOTHER market (copper; the dollar, inverted).

    The value at ``t`` uses that market's close of ``t``; the caller reads it a day late, because those
    markets close after the oil settlement (``data.known_before``).
    """
    forecast = trend_forecast(close.astype("float64").pct_change())
    return -forecast if inverse else forecast


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


def _positive_weights(weights: Mapping[str, float] | None) -> dict[str, float]:
    chosen = DEFAULT_WEIGHTS if weights is None else weights
    out = {name: float(chosen[name]) for name in SLEEVES if float(chosen.get(name, 0.0)) > 0}
    if not out:
        raise ValueError("no sleeve with a positive weight")
    return out


def _multiplier(present: float, fdm: float) -> float:
    """The diversification multiplier for ``present`` sleeves out of the seven: 1 with one sleeve, ``fdm`` with
    all of them, a straight line in between. Fewer sleeves diversify less, whether they are missing today or
    the book was never given them. Against Carver's exact formula on the measured correlations the line is
    low by 0.0-0.3 for a subset (1.50 instead of 1.61 with the macro source missing): when information is
    missing the book takes a little less risk, never more."""
    return 1.0 + (fdm - 1.0) * max(0.0, (present - 1.0) / (len(SLEEVES) - 1))


def combine(
    forecasts: Mapping[str, pd.Series],
    weights: Mapping[str, float] | None = None,
    fdm: float = COMBINED_FDM,
) -> pd.Series:
    """Weighted average of the sleeves, source by source, times the diversification multiplier, capped at +-20.

    Inside a source the sleeves present that day are averaged with their weights; the sources are then averaged
    with the weight of all their sleeves, present or not. A source therefore keeps its share when one of its
    sleeves has no value (copper before 2000, a table that failed to download), and drops out only when none
    has. The multiplier shrinks with the number of sleeves missing (``_multiplier``).
    """
    w = _positive_weights(weights)
    given = [name for name in w if name in forecasts]
    if not given:
        raise ValueError("none of the sleeves with a positive weight was given")
    index = forecasts[given[0]].index
    # a sleeve that was not given is a sleeve with no value on any day: it counts in the multiplier
    frame = pd.DataFrame({name: forecasts.get(name) for name in w}, index=index).astype("float64")
    present = frame.notna()
    numerator = pd.Series(0.0, index=index)
    denominator = pd.Series(0.0, index=index)
    for members in SOURCES.values():
        names = [name for name in members if name in w]
        if not names:
            continue
        ws = pd.Series({name: w[name] for name in names})
        source_weight = float(ws.sum())
        have = present[names].mul(ws, axis=1).sum(axis=1)
        value = frame[names].fillna(0.0).mul(ws, axis=1).sum(axis=1) / have.where(have > 0)
        numerator = numerator + (value * source_weight).fillna(0.0)
        denominator = denominator + (have > 0).astype("float64") * source_weight
    weighted = numerator / denominator.where(denominator > 0)
    multiplier = 1.0 + (fdm - 1.0) * ((present.sum(axis=1) - 1.0) / (len(SLEEVES) - 1)).clip(lower=0.0)
    return (weighted * multiplier).clip(-FORECAST_CAP, FORECAST_CAP)


def _finite(value: float | None) -> bool:
    return value is not None and math.isfinite(float(value))


def source_values(
    values: Mapping[str, float | None], weights: Mapping[str, float] | None = None
) -> dict[str, float | None]:
    """The weighted average of each source's sleeves for one day (None when the source has no value)."""
    w = _positive_weights(weights)
    out: dict[str, float | None] = {}
    for source, members in SOURCES.items():
        total = 0.0
        weight_sum = 0.0
        for name in members:
            value = values.get(name)
            if name not in w or value is None or not _finite(value):
                continue
            total += w[name] * float(value)
            weight_sum += w[name]
        if any(name in w for name in members):
            out[source] = total / weight_sum if weight_sum > 0 else None
    return out


def combine_values(
    values: Mapping[str, float | None],
    weights: Mapping[str, float] | None = None,
    fdm: float = COMBINED_FDM,
) -> float | None:
    """Scalar twin of :func:`combine` for one day: same weights, same sources, same multiplier.

    Returns None when no sleeve with a positive weight has a value.
    """
    w = _positive_weights(weights)
    by_source = source_values(values, weights)
    total = 0.0
    weight_sum = 0.0
    for source, value in by_source.items():
        if value is None:
            continue
        source_weight = sum(w[name] for name in SOURCES[source] if name in w)
        total += source_weight * value
        weight_sum += source_weight
    if weight_sum <= 0:
        return None
    present = sum(1 for name in w if _finite(values.get(name)))
    multiplier = _multiplier(float(present), fdm)
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
