"""Black-76 option pricing on futures. Used ONLY by S18 (synthetic options), which is an APPROXIMATION.

We have no real ICE Brent option quotes in `docs/DATA_SOURCES.md`, so S18 prices a synthetic structure on the
front future using OVX (30d implied vol on oil ETF options) as the at-the-money volatility. Every number produced
here is therefore an approximation and must be labelled `approx=True` in JSON / "≈" in the UI.

Model: F is the futures price, discounting at the risk-free rate r over T years (Black, 1976)::

    call = e^{-rT} [ F N(d1) - K N(d2) ]      put = e^{-rT} [ K N(-d2) - F N(-d1) ]
    d1 = (ln(F/K) + vol^2 T / 2) / (vol sqrt(T))                  d2 = d1 - vol sqrt(T)

Put-call parity holds by construction: call - put = e^{-rT} (F - K).

SKEW ASSUMPTION (declared, not fitted): crude oil puts trade above the at-the-money volatility. We add
`slope_per_10pct` volatility points (default 0.02 = 2 vol points) for every 10% the strike sits BELOW the future,
linearly and monotonically, and we model no call-wing skew (call strikes use the ATM vol). This is deliberately
crude; it exists so that S18's defined-risk structures are not priced with a flat smile, and it is reported as an
approximation wherever it is used.
"""

from __future__ import annotations

import math

from scipy.optimize import brentq
from scipy.stats import norm

# documented skew: +2 volatility points per 10% out-of-the-money on the put wing
DEFAULT_SKEW_PER_10PCT = 0.02
SKEW_REFERENCE_OTM = 0.10

_TINY = 1e-12
MIN_VOL = 1e-6
MAX_VOL = 5.0


def _total_vol(t: float, vol: float) -> float:
    """vol * sqrt(T), clipped at zero (expired or zero-vol options are intrinsic)."""
    return float(vol) * math.sqrt(max(float(t), 0.0))


def _d1_d2(f: float, k: float, t: float, vol: float) -> tuple[float, float]:
    sv = _total_vol(t, vol)
    d1 = (math.log(f / k) + 0.5 * vol * vol * t) / sv
    return d1, d1 - sv


def price(f: float, k: float, t: float, vol: float, r: float = 0.0, is_call: bool = True) -> float:
    """Black-76 price of a European option on a future. `t` in years, `vol` annualised, `r` continuous."""
    if f <= 0.0 or k <= 0.0:
        raise ValueError("Black-76 requires a strictly positive future and strike")
    df = math.exp(-float(r) * max(float(t), 0.0))
    if _total_vol(t, vol) <= _TINY:
        intrinsic = max(f - k, 0.0) if is_call else max(k - f, 0.0)
        return float(df * intrinsic)
    d1, d2 = _d1_d2(f, k, t, vol)
    if is_call:
        return float(df * (f * norm.cdf(d1) - k * norm.cdf(d2)))
    return float(df * (k * norm.cdf(-d2) - f * norm.cdf(-d1)))


def delta(f: float, k: float, t: float, vol: float, r: float = 0.0, is_call: bool = True) -> float:
    """dPrice/dF (discounted). Call delta in [0, e^{-rT}], put delta in [-e^{-rT}, 0]."""
    if f <= 0.0 or k <= 0.0:
        raise ValueError("Black-76 requires a strictly positive future and strike")
    df = math.exp(-float(r) * max(float(t), 0.0))
    if _total_vol(t, vol) <= _TINY:
        if is_call:
            return float(df) if f > k else 0.0
        return -float(df) if f < k else 0.0
    d1, _ = _d1_d2(f, k, t, vol)
    return float(df * norm.cdf(d1)) if is_call else float(-df * norm.cdf(-d1))


def vega(f: float, k: float, t: float, vol: float, r: float = 0.0) -> float:
    """dPrice/dVol (per 1.0 of annualised vol, i.e. per 100 vol points). Identical for calls and puts."""
    if f <= 0.0 or k <= 0.0:
        raise ValueError("Black-76 requires a strictly positive future and strike")
    if _total_vol(t, vol) <= _TINY:
        return 0.0
    df = math.exp(-float(r) * max(float(t), 0.0))
    d1, _ = _d1_d2(f, k, t, vol)
    return float(df * f * norm.pdf(d1) * math.sqrt(max(float(t), 0.0)))


def implied_vol(
    target_price: float,
    f: float,
    k: float,
    t: float,
    r: float = 0.0,
    is_call: bool = True,
    lo: float = MIN_VOL,
    hi: float = MAX_VOL,
) -> float | None:
    """Invert Black-76 by bracketed root finding. Returns None when the price is not attainable in [lo, hi]."""
    if t <= 0.0 or target_price <= 0.0:
        return None

    def obj(v: float) -> float:
        return price(f, k, t, v, r, is_call) - float(target_price)

    if obj(lo) > 0.0 or obj(hi) < 0.0:
        return None
    try:
        return float(brentq(obj, lo, hi, xtol=1e-10, maxiter=200))
    except (ValueError, RuntimeError):
        return None


def skew_vol(f: float, k: float, atm_vol: float, slope_per_10pct: float = DEFAULT_SKEW_PER_10PCT) -> float:
    """Volatility for strike `k` under the declared put skew (see module docstring).

    Monotonically non-increasing in `k`: strikes below the future get `slope_per_10pct` extra vol per 10% OTM,
    strikes at or above the future get the ATM vol (no call-wing skew modelled).
    """
    if f <= 0.0:
        raise ValueError("Black-76 requires a strictly positive future")
    otm = max(0.0, (f - float(k)) / f)
    return float(max(MIN_VOL, atm_vol + slope_per_10pct * otm / SKEW_REFERENCE_OTM))
