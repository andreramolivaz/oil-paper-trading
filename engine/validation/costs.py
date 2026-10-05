"""Transaction-cost sensitivity (brief §12: "costi sempre inclusi, con sensibilità a costi raddoppiati").

Units: ``cost_per_turn`` is the cost of one unit of turnover as a fraction of equity (0.0005 = 5 bps per unit of
notional traded relative to equity); ``turnover`` is the per-period notional traded divided by equity (a full flip
from +1 to -1 counts 2).  Net returns are ``gross_t - multiplier · cost_per_turn · turnover_t``.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np
import pandas as pd

from engine.validation.metrics import TRADING_DAYS, annualised_vol, cagr, calmar, max_drawdown, sharpe

__all__ = ["breakeven_cost", "cost_sensitivity", "net_returns"]


def _pair(
    returns_gross: Sequence[float] | np.ndarray | pd.Series, turnover: Sequence[float] | np.ndarray | pd.Series
) -> tuple[np.ndarray, np.ndarray]:
    g = np.asarray(returns_gross, dtype=float).ravel()
    t = np.asarray(turnover, dtype=float).ravel()
    if g.shape != t.shape:
        raise ValueError("returns_gross and turnover must have the same length")
    keep = ~(np.isnan(g) | np.isnan(t))
    return g[keep], t[keep]


def net_returns(
    returns_gross: Sequence[float] | np.ndarray | pd.Series,
    cost_per_turn: float,
    turnover: Sequence[float] | np.ndarray | pd.Series,
    multiplier: float = 1.0,
) -> np.ndarray:
    """Gross returns minus ``multiplier · cost_per_turn · turnover`` (NaN rows dropped pairwise)."""
    g, t = _pair(returns_gross, turnover)
    return g - multiplier * cost_per_turn * t


def cost_sensitivity(
    returns_gross: Sequence[float] | np.ndarray | pd.Series,
    cost_per_turn: float,
    turnover_series: Sequence[float] | np.ndarray | pd.Series,
    multipliers: Sequence[float] = (1, 2),
    periods_per_year: int = TRADING_DAYS,
) -> pd.DataFrame:
    """Sharpe / CAGR / max drawdown / Calmar / vol / total cost under each cost multiplier (rows = multipliers).

    ``multiplier = 0`` gives the gross figures; the lifecycle rule uses the row ``2`` (doubled costs).
    """
    if cost_per_turn < 0:
        raise ValueError("cost_per_turn must be >= 0")
    g, t = _pair(returns_gross, turnover_series)
    rows = []
    for m in multipliers:
        cost = float(m) * cost_per_turn * t
        net = g - cost
        rows.append(
            {
                "multiplier": float(m),
                "cost_per_turn_bps": float(m) * cost_per_turn * 1e4,
                "total_cost": float(cost.sum()),
                "ann_cost": float(cost.mean() * periods_per_year) if cost.size else float("nan"),
                "sharpe": sharpe(net, periods_per_year),
                "cagr": cagr(net, periods_per_year),
                "ann_vol": annualised_vol(net, periods_per_year),
                "max_drawdown": max_drawdown(net).depth,
                "calmar": calmar(net, periods_per_year),
            }
        )
    return pd.DataFrame(rows).set_index("multiplier")


def breakeven_cost(
    returns_gross: Sequence[float] | np.ndarray | pd.Series, turnover: Sequence[float] | np.ndarray | pd.Series
) -> float:
    """Cost per unit of turnover, in basis points, at which the mean net return is zero: ``sum(gross)/sum(turnover)``.

    Linear (arithmetic) breakeven; ``inf`` with positive gross P&L and no turnover, NaN when nothing was traded
    and nothing was earned, negative when the gross P&L is already negative.
    """
    g, t = _pair(returns_gross, turnover)
    total_turn = float(t.sum())
    total_gross = float(g.sum())
    if total_turn == 0.0:
        return float("inf") if total_gross > 0 else float("nan")
    return total_gross / total_turn * 1e4
