"""Value-at-Risk, Expected Shortfall and distance to liquidation (brief §4.2, §9, risk.json).

Sign convention
---------------
Returns are **fractions** (-0.031 = -3.1 %).  VaR and ES are reported as **positive losses**: a 1-day VaR99 of
0.075 means "a loss of 7.5 % of the notional is exceeded with 1 % probability".  Dollar figures follow the same
rule (a positive number is money lost).  Nothing here annualises: every figure is for the stated horizon.

Two estimators, deliberately kept separate:

* :func:`historical_var_es` reads the empirical distribution of a real return sample.  No distribution is
  assumed, so it cannot say anything about a loss larger than the worst day in the sample — with ~2 500 daily
  observations the 99 % tail rests on ~25 points, which is exactly why the parametric estimate exists as well.
* :func:`parametric_var_es` uses the Student-t(4) tail of :mod:`engine.portfolio.leverage`, the same tail the
  leverage rule budgets with (``sizing.es99_budget``), so the dashboard number and the sizing rule cannot
  disagree.  ``parametric_var_es(v, 0.99)[1] == es99_one_day(v, 0.99)`` by construction.

:func:`portfolio_var` aggregates positions with the covariance of whatever return columns are available; symbols
without a return series are **excluded and listed** (never proxied with another symbol's series, never filled).
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd
from scipy import stats

from engine.broker.margin import liquidation_price
from engine.portfolio.leverage import ES_DF, TRADING_DAYS, es99_one_day, student_t_es

__all__ = [
    "PortfolioVaR",
    "historical_var_es",
    "liquidation_distance",
    "parametric_var_es",
    "portfolio_var",
    "student_t_var_multiple",
]

NAN = float("nan")


def _clean(x: Sequence[float] | np.ndarray | pd.Series) -> np.ndarray:
    a = np.asarray(x, dtype=float).ravel()
    return a[np.isfinite(a)]


def student_t_var_multiple(level: float = 0.99, df: float = ES_DF) -> float:
    """Quantile of a **unit-variance** Student-t at ``level``, as a positive multiple of sigma.

    The raw t quantile is scaled by ``sqrt((df - 2) / df)`` so the distribution has standard deviation 1, exactly
    as :func:`engine.portfolio.leverage.student_t_es` does for the expected shortfall.
    """
    if not 0.0 < level < 1.0:
        raise ValueError("level must be in (0, 1)")
    if df <= 2.0:
        raise ValueError("df must be > 2 for a finite variance")
    return float(stats.t.ppf(level, df)) * math.sqrt((df - 2.0) / df)


def historical_var_es(returns: Sequence[float] | np.ndarray | pd.Series, level: float = 0.99) -> tuple[float, float]:
    """Empirical ``(var, es)`` at ``level``, as positive loss fractions.

    ``var`` is minus the ``1 - level`` quantile of the sample (linear interpolation, the numpy default) and ``es``
    is minus the mean of the observations at or below that quantile.  Non-finite observations are dropped (never
    filled).  ``(nan, nan)`` when fewer than two usable observations.  The estimate is only as good as the sample:
    at ``level = 0.99`` roughly ``n / 100`` observations carry it.
    """
    if not 0.0 < level < 1.0:
        raise ValueError("level must be in (0, 1)")
    r = _clean(returns)
    if r.size < 2:
        return NAN, NAN
    q = float(np.quantile(r, 1.0 - level))
    tail = r[r <= q]
    es = float(tail.mean()) if tail.size else q
    return -q, -es


def parametric_var_es(
    vol_annual: float, level: float = 0.99, df: float = ES_DF, horizon_days: float = 1.0
) -> tuple[float, float]:
    """Student-t ``(var, es)`` as positive loss fractions of the notional, from an annualised volatility.

    ``sigma_h = vol_annual / sqrt(252) * sqrt(horizon_days)``; the tail is the unit-variance Student-t with ``df``
    degrees of freedom (4 by default: fat, as Brent gaps demand).  With ``horizon_days = 1`` the ES is literally
    :func:`engine.portfolio.leverage.es99_one_day`, which is the quantity ``sizing.es99_budget`` caps.
    ``(inf, inf)`` for a non-finite or non-positive volatility (an unusable input must never look safe).
    """
    if horizon_days <= 0:
        raise ValueError("horizon_days must be > 0")
    es_1d = es99_one_day(vol_annual, level, df)
    if not math.isfinite(es_1d):
        return math.inf, math.inf
    scale = math.sqrt(float(horizon_days))
    daily = float(vol_annual) / math.sqrt(TRADING_DAYS)
    return daily * scale * student_t_var_multiple(level, df), es_1d * scale


@dataclass
class PortfolioVaR:
    """Portfolio VaR/ES over one day, with the per-position contributions that add up to the total.

    ``var``/``es`` are positive dollar losses; ``var_fraction``/``es_fraction`` divide them by ``equity`` when an
    equity is given.  ``contributions[symbol]`` holds the signed notional and the component VaR/ES
    ``c_i = n_i (Σn)_i / sigma``, which sum to the portfolio figure (Euler decomposition, valid for any
    elliptical tail).  ``excluded`` lists the symbols dropped for want of a return series, with the reason.
    """

    level: float
    df: float
    method: str
    var: float
    es: float
    sigma: float  # 1-day P&L standard deviation in USD
    gross_notional: float
    net_notional: float
    equity: float | None = None
    n_obs: int = 0
    contributions: dict[str, dict[str, float]] = field(default_factory=dict)
    excluded: list[dict[str, Any]] = field(default_factory=list)

    @property
    def var_fraction(self) -> float:
        return self.var / self.equity if self.equity else NAN

    @property
    def es_fraction(self) -> float:
        return self.es / self.equity if self.equity else NAN

    def to_dict(self) -> dict[str, Any]:
        def f(x: float) -> float | None:
            return None if x is None or not math.isfinite(x) else float(x)

        return {
            "level": self.level,
            "df": self.df,
            "method": self.method,
            "var": f(self.var),
            "es": f(self.es),
            "var_fraction": f(self.var_fraction),
            "es_fraction": f(self.es_fraction),
            "sigma_1d": f(self.sigma),
            "gross_notional": f(self.gross_notional),
            "net_notional": f(self.net_notional),
            "n_obs": self.n_obs,
            "contributions": {k: {kk: f(vv) for kk, vv in v.items()} for k, v in sorted(self.contributions.items())},
            "excluded": list(self.excluded),
            "labels": {
                "var": f"VaR {self.level:.0%} 1 giorno",
                "es": f"Expected shortfall {self.level:.0%} 1 giorno",
                "contributions": "Contributo per posizione",
                "excluded": "Simboli esclusi (nessuna serie di rendimenti)",
            },
        }


def portfolio_var(
    positions: Mapping[str, float],
    prices: Mapping[str, float],
    returns: pd.DataFrame,
    level: float = 0.99,
    df: float = ES_DF,
    method: str = "parametric",
    equity: float | None = None,
    min_obs: int = 20,
) -> dict[str, Any]:
    """1-day VaR/ES of a book of futures positions, in USD, with per-position contributions.

    Parameters
    ----------
    positions
        ``symbol -> signed quantity in barrels`` (negative = short).  Zero quantities are ignored.
    prices
        ``symbol -> current price in USD/bbl``.  A symbol without a positive finite price is excluded.
    returns
        Daily **simple** returns, one column per symbol, index aligned across columns.  Rows with any NaN among
        the used columns are dropped pairwise-free (complete cases), so the covariance is internally consistent.
    method
        ``"parametric"`` (default) scales the portfolio standard deviation by the unit-variance Student-t(``df``)
        quantile and ES multiple — the same tail the leverage budget uses.  ``"historical"`` instead revalues the
        book over every historical day and reads the empirical quantile; contributions stay the covariance-based
        Euler decomposition (documented in :class:`PortfolioVaR`) because an empirical quantile has no additive
        per-position split.

    Symbols with a position and a price but **no return column** (or too few observations, ``min_obs``) are
    excluded from the risk figure and listed in ``excluded`` with a reason.  They are NOT proxied with another
    symbol's series and their risk is NOT invented: a caller showing the number must show the exclusions too.
    Returns :meth:`PortfolioVaR.to_dict` (JSON-friendly, non-finite values as ``None``).
    """
    if method not in {"parametric", "historical"}:
        raise ValueError("method must be 'parametric' or 'historical'")
    rdf = returns if isinstance(returns, pd.DataFrame) else pd.DataFrame(returns)

    notionals: dict[str, float] = {}
    excluded: list[dict[str, Any]] = []
    for sym, qty in positions.items():
        q = float(qty)
        if q == 0.0:
            continue
        px = prices.get(sym)
        if px is None or not math.isfinite(float(px)) or float(px) <= 0:
            excluded.append({"symbol": sym, "reason": "prezzo non disponibile", "qty_bbl": q})
            continue
        if sym not in rdf.columns:
            excluded.append({"symbol": sym, "reason": "nessuna serie di rendimenti", "qty_bbl": q})
            continue
        col = pd.to_numeric(rdf[sym], errors="coerce")
        if int(col.replace([np.inf, -np.inf], np.nan).dropna().size) < min_obs:
            excluded.append({"symbol": sym, "reason": f"meno di {min_obs} osservazioni", "qty_bbl": q})
            continue
        notionals[sym] = q * float(px)

    used = sorted(notionals)
    out = PortfolioVaR(
        level=level,
        df=df,
        method=method,
        var=NAN,
        es=NAN,
        sigma=NAN,
        gross_notional=float(sum(abs(v) for v in notionals.values())),
        net_notional=float(sum(notionals.values())),
        equity=None if equity is None else float(equity),
        excluded=excluded,
    )
    if not used:
        return out.to_dict()

    sub = rdf[used].apply(pd.to_numeric, errors="coerce").replace([np.inf, -np.inf], np.nan).dropna(how="any")
    out.n_obs = len(sub)
    if out.n_obs < max(2, min_obs):
        for sym in used:
            excluded.append(
                {"symbol": sym, "reason": "troppe poche righe complete in comune", "qty_bbl": positions[sym]}
            )
        out.contributions = {}
        out.excluded = excluded
        return out.to_dict()

    n = np.array([notionals[s] for s in used], dtype=float)
    cov = np.asarray(sub.cov(ddof=1).to_numpy(), dtype=float)
    sigma_sq = float(n @ cov @ n)
    sigma = math.sqrt(max(sigma_sq, 0.0))
    out.sigma = sigma

    k_var = student_t_var_multiple(level, df)
    k_es = student_t_es(level, df)
    if method == "parametric":
        out.var, out.es = sigma * k_var, sigma * k_es
    else:
        pnl = sub.to_numpy(dtype=float) @ n  # USD P&L of today's book on each historical day
        var_f, es_f = historical_var_es(pnl, level)
        out.var, out.es = var_f, es_f

    if sigma > 0:
        marginal = cov @ n / sigma  # d sigma / d n_i
        share_var = out.var / sigma if math.isfinite(out.var) else NAN
        share_es = out.es / sigma if math.isfinite(out.es) else NAN
        for i, sym in enumerate(used):
            comp = float(n[i] * marginal[i])
            out.contributions[sym] = {
                "notional": float(n[i]),
                "qty_bbl": float(positions[sym]),
                "sigma_contribution": comp,
                "var_contribution": comp * share_var,
                "es_contribution": comp * share_es,
                "share": comp / sigma,
            }
    return out.to_dict()


def liquidation_distance(
    qty_bbl: float,
    avg_price: float,
    cash: float,
    margin_rate: float,
    stop_out_level: float,
    reference_price: float | None = None,
    other_unrealized: float = 0.0,
    other_margin: float = 0.0,
) -> tuple[float | None, float | None]:
    """``(liquidation price, signed fractional move to it)`` for one net position.

    Delegates the price to :func:`engine.broker.margin.liquidation_price` (the broker's own arithmetic: one
    formula, one place).  The move is measured from ``reference_price`` when given, else from ``avg_price``:
    negative for a long (the price has to fall), positive for a short.  ``(None, None)`` when there is no
    liquidation price (flat, or a position so light that the margin level never reaches ``stop_out_level``).
    """
    price = liquidation_price(
        float(qty_bbl),
        float(avg_price),
        float(cash),
        float(margin_rate),
        float(stop_out_level),
        other_unrealized=float(other_unrealized),
        other_margin=float(other_margin),
    )
    if price is None:
        return None, None
    ref = float(reference_price) if reference_price is not None else float(avg_price)
    if not math.isfinite(ref) or ref <= 0:
        return price, None
    return price, (price - ref) / ref
