"""Fractional Kelly sizing.

Kelly for a continuous bet is f* = mu / sigma^2 (both over the SAME horizon). We never use full Kelly: the
inputs (expected return above all) are estimates, and Kelly is famously sensitive to them — overestimating mu by
a factor of two turns the growth-optimal bet into a ruinous one. `fraction` (config `sizing.kelly_fraction`,
default 0.25) is the shrinkage, and the result is additionally capped by the caller (vol, drawdown, event, 10x).
"""

from __future__ import annotations

import math

TRADING_DAYS = 252


def kelly_leverage(expected_return: float, variance: float) -> float:
    """Full-Kelly leverage for an expected return and variance measured over the same horizon."""
    if not math.isfinite(expected_return) or not math.isfinite(variance) or variance <= 0:
        return 0.0
    return float(expected_return / variance)


def fractional_kelly_leverage(
    expected_return: float,
    expected_vol: float,
    fraction: float,
    horizon_days: int = 1,
    annualised: bool = False,
) -> float:
    """Fractional-Kelly leverage, never negative.

    `expected_return` and `expected_vol` are over the horizon (fractions of notional) unless `annualised` is
    True, in which case both are annual and are converted to the horizon first. The sign of the position comes
    from the signal, not from here: this returns a magnitude.
    """
    if not math.isfinite(expected_return) or not math.isfinite(expected_vol) or expected_vol <= 0:
        return 0.0
    mu, sigma = abs(float(expected_return)), float(expected_vol)
    if annualised:
        scale = math.sqrt(max(1, horizon_days) / TRADING_DAYS)
        mu *= max(1, horizon_days) / TRADING_DAYS
        sigma *= scale
    lev = kelly_leverage(mu, sigma * sigma)
    return float(max(0.0, fraction * lev))
