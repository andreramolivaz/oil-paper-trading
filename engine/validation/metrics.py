"""Performance statistics on a daily return series (brief §12).

Conventions
-----------
* ``returns`` are simple per-period returns (fractions: 0.012 = +1.2 %) at daily frequency unless
  ``periods_per_year`` says otherwise.  ``NaN`` are dropped before any computation (never filled).
* Sharpe, Sortino and volatility are annualised with ``sqrt(periods_per_year)``; the CAGR compounds.
* ``probabilistic_sharpe_ratio`` and ``min_track_record_length`` work in **per-observation** units: the Sharpe
  ratios must be at the same frequency as ``n`` (daily SR with ``n`` days, monthly SR with ``n`` months).
  ``skew`` is the third standardised moment and ``kurt`` the Pearson (non-excess) kurtosis, 3 for a Gaussian,
  exactly as in Bailey & López de Prado (2012), "The Sharpe Ratio Efficient Frontier", J. of Risk 15(2).
* Drawdown indices refer to the equity curve ``e_0 = 1, e_t = prod(1 + r_1..r_t)`` (``t = 0..n``).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from typing import Any

import numpy as np
import pandas as pd
from scipy import stats

type ArrayLike = Sequence[float] | np.ndarray | pd.Series

__all__ = [
    "Drawdown",
    "annualised_vol",
    "cagr",
    "calmar",
    "drawdown_series",
    "hit_rate",
    "kurtosis",
    "max_drawdown",
    "min_track_record_length",
    "probabilistic_sharpe_ratio",
    "profit_factor",
    "sharpe",
    "skew",
    "sortino",
    "summary",
    "turnover",
]

TRADING_DAYS = 252
NAN = float("nan")


def _clean(x: ArrayLike) -> np.ndarray:
    """1-D float array with NaN removed (dropped, never filled)."""
    a = np.asarray(x, dtype=float).ravel()
    return a[~np.isnan(a)]


def _finite(x: float) -> bool:
    return math.isfinite(x)


# ---------------------------------------------------------------------------------------------------------------
# return / risk
# ---------------------------------------------------------------------------------------------------------------
def annualised_vol(returns: ArrayLike, periods_per_year: int = TRADING_DAYS) -> float:
    r = _clean(returns)
    if r.size < 2:
        return NAN
    return float(r.std(ddof=1) * math.sqrt(periods_per_year))


def cagr(returns: ArrayLike, periods_per_year: int = TRADING_DAYS) -> float:
    """Compound annual growth rate; -1.0 when the equity curve is wiped out."""
    r = _clean(returns)
    if r.size == 0:
        return NAN
    total = float(np.prod(1.0 + r))
    if total <= 0.0:
        return -1.0
    years = r.size / periods_per_year
    return float(total ** (1.0 / years) - 1.0)


def sharpe(returns: ArrayLike, periods_per_year: int = TRADING_DAYS, rf: float = 0.0) -> float:
    """Annualised Sharpe ratio ``mean / std(ddof=1) * sqrt(periods)``; ``rf`` is an annual risk-free rate.

    Returns NaN when fewer than two observations or when the standard deviation is zero.
    """
    r = _clean(returns) - rf / periods_per_year
    if r.size < 2:
        return NAN
    sd = float(r.std(ddof=1))
    if sd == 0.0:
        return NAN
    return float(r.mean() / sd * math.sqrt(periods_per_year))


def sortino(returns: ArrayLike, periods_per_year: int = TRADING_DAYS, target: float = 0.0) -> float:
    """Annualised Sortino ratio with the target downside deviation ``sqrt(mean(min(r - target, 0)^2))``.

    ``inf`` when there is no downside and the mean excess return is positive, NaN otherwise.
    """
    r = _clean(returns)
    if r.size < 2:
        return NAN
    excess = r - target / periods_per_year
    downside = float(np.sqrt(np.mean(np.minimum(excess, 0.0) ** 2)))
    mean = float(excess.mean())
    if downside == 0.0:
        return math.inf if mean > 0 else NAN
    return float(mean / downside * math.sqrt(periods_per_year))


# ---------------------------------------------------------------------------------------------------------------
# drawdown
# ---------------------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Drawdown:
    """Deepest drawdown of an equity curve.

    ``depth`` is a negative fraction (-0.25 = -25 %), 0.0 when the curve never dips below a previous peak.
    ``duration`` counts periods from the peak preceding the deepest trough to the recovery (or to the end of the
    sample when not recovered).  ``longest_underwater`` is the longest spell below any previous peak, which need
    not be the deepest one.  Indices refer to the equity curve ``e_0 = 1`` (see module docstring).
    """

    depth: float
    duration: int
    peak_idx: int
    trough_idx: int
    recovery_idx: int | None
    longest_underwater: int

    @property
    def recovered(self) -> bool:
        return self.recovery_idx is not None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _equity(returns: np.ndarray, compound: bool) -> np.ndarray:
    growth = np.cumprod(1.0 + returns) if compound else 1.0 + np.cumsum(returns)
    return np.concatenate(([1.0], growth))


def drawdown_series(returns: ArrayLike, compound: bool = True) -> np.ndarray:
    """Drawdown ``e_t / max_{s<=t} e_s - 1`` for ``t = 0..n`` (first element is 0)."""
    e = _equity(_clean(returns), compound)
    peak = np.maximum.accumulate(e)
    with np.errstate(divide="ignore", invalid="ignore"):
        dd = np.where(peak > 0, e / peak - 1.0, -1.0)
    return np.minimum(dd, 0.0)


def max_drawdown(returns: ArrayLike, compound: bool = True) -> Drawdown:
    dd = drawdown_series(returns, compound)
    n = dd.size - 1
    if n <= 0 or not np.any(dd < 0):
        return Drawdown(0.0, 0, 0, 0, 0, 0)
    trough = int(np.argmin(dd))
    peaks_before = np.flatnonzero(dd[:trough] == 0.0)
    peak = int(peaks_before[-1])  # dd[0] == 0 guarantees at least one
    after = np.flatnonzero(dd[trough + 1 :] >= 0.0)
    recovery: int | None = int(trough + 1 + after[0]) if after.size else None
    duration = (recovery if recovery is not None else n) - peak

    under = dd < 0.0
    padded = np.concatenate(([False], under, [False]))
    edges = np.flatnonzero(np.diff(padded.astype(np.int8)))
    starts, ends = edges[0::2], edges[1::2]  # runs occupy [start, end)
    longest = int(max(min(int(e), n) - (int(s) - 1) for s, e in zip(starts, ends, strict=True)))
    return Drawdown(float(dd[trough]), int(duration), peak, trough, recovery, longest)


def calmar(returns: ArrayLike, periods_per_year: int = TRADING_DAYS) -> float:
    """CAGR divided by the absolute maximum drawdown; ``inf`` when there is no drawdown and the CAGR is positive."""
    growth = cagr(returns, periods_per_year)
    depth = max_drawdown(returns).depth
    if not _finite(growth):
        return NAN
    if depth == 0.0:
        return math.inf if growth > 0 else NAN
    return float(growth / abs(depth))


# ---------------------------------------------------------------------------------------------------------------
# trade statistics
# ---------------------------------------------------------------------------------------------------------------
def hit_rate(pnl: ArrayLike) -> float:
    """Fraction of strictly positive outcomes (daily returns or per-trade P&L)."""
    x = _clean(pnl)
    if x.size == 0:
        return NAN
    return float(np.mean(x > 0.0))


def profit_factor(trades: ArrayLike) -> float:
    """Gross wins divided by gross losses over per-trade P&L; ``inf`` with wins and no losses, NaN when empty."""
    t = _clean(trades)
    if t.size == 0:
        return NAN
    wins = float(t[t > 0].sum())
    losses = float(-t[t < 0].sum())
    if losses == 0.0:
        return math.inf if wins > 0 else NAN
    return wins / losses


def turnover(positions: ArrayLike, periods_per_year: int = TRADING_DAYS) -> float:
    """Annualised turnover of a position/weight series: ``sum(|Δpos|) / n * periods_per_year``.

    The first position counts as a change from flat.  With positions expressed as a fraction of equity, 2.0 means
    the book is rebuilt twice per year on average (a full flip +1 → -1 counts 2).
    """
    p = _clean(positions)
    if p.size == 0:
        return NAN
    delta = np.abs(np.diff(p, prepend=0.0))
    return float(delta.sum() / p.size * periods_per_year)


# ---------------------------------------------------------------------------------------------------------------
# higher moments and the Probabilistic Sharpe Ratio
# ---------------------------------------------------------------------------------------------------------------
def skew(returns: ArrayLike) -> float:
    r = _clean(returns)
    if r.size < 3 or r.std() == 0.0:
        return NAN
    return float(stats.skew(r, bias=True))


def kurtosis(returns: ArrayLike, excess: bool = False) -> float:
    """Pearson kurtosis (3 for a Gaussian) or, with ``excess=True``, Fisher's excess kurtosis (0 for a Gaussian)."""
    r = _clean(returns)
    if r.size < 4 or r.std() == 0.0:
        return NAN
    return float(stats.kurtosis(r, fisher=excess, bias=True))


def _psr_denominator(sr_hat: float, skew_: float, kurt: float) -> float:
    """``1 - γ3·SR + (γ4 - 1)/4 · SR²``: variance inflation of the SR estimator under non-normality."""
    return 1.0 - skew_ * sr_hat + (kurt - 1.0) / 4.0 * sr_hat**2


def probabilistic_sharpe_ratio(
    sr_hat: float, sr_benchmark: float, n: int, skew: float = 0.0, kurt: float = 3.0
) -> float:
    """PSR of Bailey & López de Prado (2012): probability that the true Sharpe exceeds ``sr_benchmark``.

    ``PSR = Φ[ (SR̂ - SR*) · sqrt(n - 1) / sqrt(1 - γ3·SR̂ + (γ4 - 1)/4 · SR̂²) ]`` with ``SR̂`` and ``SR*`` in
    per-observation units, ``γ3`` the skewness and ``γ4`` the Pearson kurtosis of the returns.  NaN when ``n < 2``,
    any input is not finite, or the variance term is not positive.
    """
    if n < 2 or not all(_finite(v) for v in (sr_hat, sr_benchmark, skew, kurt)):
        return NAN
    denom_sq = _psr_denominator(sr_hat, skew, kurt)
    if denom_sq <= 0.0:
        return NAN
    z = (sr_hat - sr_benchmark) * math.sqrt(n - 1) / math.sqrt(denom_sq)
    return float(stats.norm.cdf(z))


def min_track_record_length(
    sr_hat: float, sr_benchmark: float, skew: float = 0.0, kurt: float = 3.0, prob: float = 0.95
) -> float:
    """Observations needed for ``PSR(sr_benchmark) >= prob`` (Bailey & López de Prado 2012, eq. 13).

    ``MinTRL = 1 + [1 - γ3·SR̂ + (γ4 - 1)/4 · SR̂²] · (Z_prob / (SR̂ - SR*))²``; ``inf`` when ``SR̂ <= SR*``.
    """
    if not all(_finite(v) for v in (sr_hat, sr_benchmark, skew, kurt)) or not 0.0 < prob < 1.0:
        return NAN
    if sr_hat <= sr_benchmark:
        return math.inf
    denom_sq = _psr_denominator(sr_hat, skew, kurt)
    if denom_sq <= 0.0:
        return NAN
    z = float(stats.norm.ppf(prob))
    return float(1.0 + denom_sq * (z / (sr_hat - sr_benchmark)) ** 2)


# ---------------------------------------------------------------------------------------------------------------
# summary
# ---------------------------------------------------------------------------------------------------------------
def _trade_pnl(trades: ArrayLike | pd.DataFrame | None) -> np.ndarray | None:
    if trades is None:
        return None
    if isinstance(trades, pd.DataFrame):
        if "pnl" not in trades.columns:
            raise ValueError("trades DataFrame needs a 'pnl' column")
        return _clean(trades["pnl"])
    return _clean(trades)


def summary(
    returns: ArrayLike,
    trades: ArrayLike | pd.DataFrame | None = None,
    positions: ArrayLike | None = None,
    periods_per_year: int = TRADING_DAYS,
) -> dict[str, Any]:
    """All metrics in one JSON-friendly dict (NaN/inf are passed through as floats).

    ``trades`` is a sequence of per-trade P&L (or a DataFrame with a ``pnl`` column); ``positions`` an optional
    position series for the turnover.  ``psr`` and ``min_trl`` are computed against a zero Sharpe benchmark in
    per-observation units.
    """
    r = _clean(returns)
    dd = max_drawdown(r)
    sr_ann = sharpe(r, periods_per_year)
    sr_obs = sr_ann / math.sqrt(periods_per_year) if _finite(sr_ann) else NAN
    sk, ku = skew(r), kurtosis(r)
    out: dict[str, Any] = {
        "n_obs": int(r.size),
        "periods_per_year": periods_per_year,
        "total_return": float(np.prod(1.0 + r) - 1.0) if r.size else NAN,
        "cagr": cagr(r, periods_per_year),
        "ann_vol": annualised_vol(r, periods_per_year),
        "sharpe": sr_ann,
        "sortino": sortino(r, periods_per_year),
        "calmar": calmar(r, periods_per_year),
        "max_drawdown": dd.depth,
        "max_drawdown_duration": dd.duration,
        "longest_underwater": dd.longest_underwater,
        "hit_rate": hit_rate(r),
        "skew": sk,
        "kurtosis": ku,
        "excess_kurtosis": ku - 3.0 if _finite(ku) else NAN,
        "psr": probabilistic_sharpe_ratio(
            sr_obs, 0.0, int(r.size), sk if _finite(sk) else 0.0, ku if _finite(ku) else 3.0
        ),
        "min_trl": min_track_record_length(sr_obs, 0.0, sk if _finite(sk) else 0.0, ku if _finite(ku) else 3.0),
    }
    if positions is not None:
        out["turnover"] = turnover(positions, periods_per_year)
    pnl = _trade_pnl(trades)
    if pnl is not None:
        wins, losses = pnl[pnl > 0], pnl[pnl < 0]
        out.update(
            {
                "n_trades": int(pnl.size),
                "trade_hit_rate": hit_rate(pnl),
                "profit_factor": profit_factor(pnl),
                "avg_trade": float(pnl.mean()) if pnl.size else NAN,
                "avg_win": float(wins.mean()) if wins.size else NAN,
                "avg_loss": float(losses.mean()) if losses.size else NAN,
            }
        )
    return out
