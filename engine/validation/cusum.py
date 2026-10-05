"""Live performance-decay detection (brief §12: "ritirata quando il decadimento è statisticamente rilevato").

* :func:`cusum_filter`: one-sided lower CUSUM on the daily shadow P&L standardised by the backtest expectation
  ``z_t = (r_t - mu_bt) / sigma_bt``:  ``S_t = max(0, S_{t-1} - z_t - k)``, alarm when ``S_t > h``.  With the
  defaults ``k = 0.5`` (half the shift to detect, in sigma units) and ``h = 5`` the in-control average run length
  is roughly 900 observations for Gaussian noise, and a drop of one sigma in the mean is caught in about ten.
* :func:`structural_break_test`: Welch two-sample t-test of the live mean against the backtest mean (H1: live is
  lower), together with the minimum live sample size needed to detect a given decay at the chosen power — a
  short live window cannot "fail" to reject just because it is short.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from typing import Any

import numpy as np
import pandas as pd
from scipy import stats

__all__ = ["BreakTest", "CusumResult", "cusum_filter", "min_live_sample_size", "structural_break_test"]


@dataclass(frozen=True)
class CusumResult:
    """CUSUM statistic aligned with the input index and the labels (dates) where it crossed ``h``."""

    statistic: pd.Series
    alarms: list[Any]
    k: float
    h: float
    mu_bt: float
    sigma_bt: float

    @property
    def triggered(self) -> bool:
        return len(self.alarms) > 0

    @property
    def first_alarm(self) -> Any | None:
        return self.alarms[0] if self.alarms else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "k": self.k,
            "h": self.h,
            "mu_bt": self.mu_bt,
            "sigma_bt": self.sigma_bt,
            "last_statistic": float(self.statistic.iloc[-1]) if len(self.statistic) else 0.0,
            "n_obs": len(self.statistic),
            "alarms": [str(a) for a in self.alarms],
            "triggered": self.triggered,
        }


def cusum_filter(
    pnl: Sequence[float] | np.ndarray | pd.Series,
    mu_bt: float,
    sigma_bt: float,
    k: float = 0.5,
    h: float = 5.0,
    reset_on_alarm: bool = True,
) -> CusumResult:
    """One-sided lower CUSUM of the standardised live P&L; see the module docstring for the recursion.

    ``pnl`` is the daily live (shadow) P&L or return series in the same units as ``mu_bt``/``sigma_bt`` (the
    backtest daily mean and standard deviation).  NaN observations carry the statistic unchanged.  After an alarm
    the statistic restarts from zero (``reset_on_alarm``) so that repeated decays produce repeated alarm dates.
    """
    if sigma_bt <= 0.0 or not math.isfinite(sigma_bt):
        raise ValueError("sigma_bt must be a positive finite number")
    if k < 0.0 or h <= 0.0:
        raise ValueError("k must be >= 0 and h > 0")
    series = pnl if isinstance(pnl, pd.Series) else pd.Series(np.asarray(pnl, dtype=float))
    values = series.to_numpy(dtype=float)
    out = np.zeros(values.size)
    alarms: list[Any] = []
    s = 0.0
    for i, r in enumerate(values):
        if math.isfinite(r):
            s = max(0.0, s - (r - mu_bt) / sigma_bt - k)
        out[i] = s
        if s > h:
            alarms.append(series.index[i])
            if reset_on_alarm:
                s = 0.0
    return CusumResult(
        pd.Series(out, index=series.index, name="cusum"), alarms, float(k), float(h), float(mu_bt), float(sigma_bt)
    )


def min_live_sample_size(effect_size: float, alpha: float = 0.05, power: float = 0.8, n_bt: int | None = None) -> float:
    """Live observations needed to detect a mean decay of ``effect_size`` sigma (one-sided, level ``alpha``).

    Known-variance approximation ``n = ((z_alpha + z_power) / d)²``.  With ``n_bt`` the backtest sample size the
    two-sample variance ``σ²(1/n_live + 1/n_bt)`` is used instead; the result is ``inf`` when the backtest
    sample alone is too short to detect the effect at that power.
    """
    if effect_size <= 0.0 or not math.isfinite(effect_size):
        raise ValueError("effect_size must be positive")
    if not (0.0 < alpha < 1.0 and 0.0 < power < 1.0):
        raise ValueError("alpha and power must be in (0, 1)")
    z = float(stats.norm.ppf(1.0 - alpha) + stats.norm.ppf(power))
    base = (z / effect_size) ** 2
    if n_bt is None:
        return float(math.ceil(base))
    denom = 1.0 / base - 1.0 / n_bt
    if denom <= 0.0:
        return math.inf
    return float(math.ceil(1.0 / denom))


@dataclass(frozen=True)
class BreakTest:
    """Welch test of ``mean(live) < mu_bt`` plus the power-based minimum live sample size.

    ``significant`` requires both ``p_value < alpha`` and ``sufficient`` (``n_live >= n_required``), so a rejection
    on a handful of days never counts as a detected break.
    """

    n_live: int
    mean_live: float
    std_live: float
    mu_bt: float
    sigma_bt: float
    n_bt: int
    t_stat: float
    dof: float
    p_value: float
    effect_size: float
    n_required: float
    sufficient: bool
    significant: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def structural_break_test(
    live: Sequence[float] | np.ndarray | pd.Series,
    mu_bt: float,
    sigma_bt: float,
    n_bt: int,
    alpha: float = 0.05,
    power: float = 0.8,
    min_effect: float | None = None,
) -> BreakTest:
    """Welch two-sample t-test (one-sided, H1: live mean below the backtest mean) with its sample-size requirement.

    ``min_effect`` is the decay to detect in sigma units; by default the full loss of the backtest edge
    (``mu_bt / sigma_bt``) when ``mu_bt > 0``, otherwise half a sigma.
    """
    if sigma_bt <= 0.0 or n_bt < 2:
        raise ValueError("sigma_bt must be positive and n_bt >= 2")
    x = np.asarray(live, dtype=float).ravel()
    x = x[np.isfinite(x)]
    n = int(x.size)
    effect = min_effect if min_effect is not None else (mu_bt / sigma_bt if mu_bt > 0 else 0.5)
    n_required = min_live_sample_size(effect, alpha, power, n_bt)
    if n < 2:
        return BreakTest(
            n,
            float(x.mean()) if n else math.nan,
            math.nan,
            mu_bt,
            sigma_bt,
            n_bt,
            math.nan,
            math.nan,
            math.nan,
            effect,
            n_required,
            False,
            False,
        )
    mean_live, var_live = float(x.mean()), float(x.var(ddof=1))
    se_sq = var_live / n + sigma_bt**2 / n_bt
    if se_sq <= 0.0:
        t_stat, dof, p_value = math.nan, math.nan, math.nan
    else:
        t_stat = (mean_live - mu_bt) / math.sqrt(se_sq)
        dof = se_sq**2 / ((var_live / n) ** 2 / (n - 1) + (sigma_bt**2 / n_bt) ** 2 / (n_bt - 1))
        p_value = float(stats.t.cdf(t_stat, dof))
    sufficient = n >= n_required
    significant = bool(sufficient and math.isfinite(p_value) and p_value < alpha)
    return BreakTest(
        n_live=n,
        mean_live=mean_live,
        std_live=math.sqrt(var_live),
        mu_bt=float(mu_bt),
        sigma_bt=float(sigma_bt),
        n_bt=int(n_bt),
        t_stat=float(t_stat),
        dof=float(dof),
        p_value=float(p_value),
        effect_size=float(effect),
        n_required=float(n_required),
        sufficient=bool(sufficient),
        significant=significant,
    )
