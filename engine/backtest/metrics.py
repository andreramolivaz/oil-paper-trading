"""Performance metrics for equity curves and trade logs (dashboard leaderboard and backtest report).

Annualisation uses 252 trading days. These are descriptive statistics only: the anti-overfitting machinery
(PSR, DSR, PBO, CUSUM) lives in `engine/validation` and is what decides whether a strategy may carry weight.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

TRADING_DAYS = 252


@dataclass
class PerfSummary:
    n_obs: int = 0
    start_equity: float = 0.0
    end_equity: float = 0.0
    total_return: float = 0.0
    cagr: float = 0.0
    ann_vol: float = 0.0
    sharpe: float = 0.0
    sortino: float = 0.0
    max_drawdown: float = 0.0  # negative fraction
    max_drawdown_days: int = 0
    calmar: float = 0.0
    hit_rate: float = 0.0
    profit_factor: float = 0.0
    n_trades: int = 0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    turnover: float = 0.0
    exposure: float = 0.0
    best_day: float = 0.0
    worst_day: float = 0.0
    by_year: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = dict(self.__dict__)
        return {k: (None if isinstance(v, float) and not math.isfinite(v) else v) for k, v in d.items()}


def _clean_returns(returns: pd.Series) -> pd.Series:
    r = pd.to_numeric(pd.Series(returns), errors="coerce").replace([np.inf, -np.inf], np.nan).dropna()
    return r.astype(float)


def returns_from_equity(equity: pd.Series) -> pd.Series:
    eq = pd.to_numeric(pd.Series(equity), errors="coerce").dropna().astype(float)
    eq = eq[eq > 0]
    return eq.pct_change().dropna() if len(eq) > 1 else pd.Series(dtype=float)


def ann_vol(returns: pd.Series) -> float:
    r = _clean_returns(returns)
    if len(r) < 2:
        return 0.0
    return float(r.std(ddof=1) * math.sqrt(TRADING_DAYS))


def sharpe(returns: pd.Series, risk_free_daily: float = 0.0) -> float:
    r = _clean_returns(returns) - risk_free_daily
    if len(r) < 2:
        return 0.0
    sd = float(r.std(ddof=1))
    if sd <= 0:
        return 0.0
    return float(r.mean() / sd * math.sqrt(TRADING_DAYS))


def sortino(returns: pd.Series, target_daily: float = 0.0) -> float:
    r = _clean_returns(returns) - target_daily
    if len(r) < 2:
        return 0.0
    downside = r[r < 0]
    if downside.empty:
        return float("inf") if r.mean() > 0 else 0.0
    dd = float(math.sqrt(float((downside**2).mean())))
    if dd <= 0:
        return 0.0
    return float(r.mean() / dd * math.sqrt(TRADING_DAYS))


def drawdown_curve(equity: pd.Series) -> pd.Series:
    eq = pd.to_numeric(pd.Series(equity), errors="coerce").dropna().astype(float)
    if eq.empty:
        return pd.Series(dtype=float)
    peak = eq.cummax()
    return eq / peak.replace(0.0, np.nan) - 1.0


def max_drawdown(equity: pd.Series) -> tuple[float, int]:
    """(depth as a negative fraction, longest underwater stretch in observations)."""
    dd = drawdown_curve(equity)
    if dd.empty:
        return 0.0, 0
    depth = float(dd.min())
    underwater = (dd < -1e-12).to_numpy()
    longest = cur = 0
    for flag in underwater:
        cur = cur + 1 if flag else 0
        longest = max(longest, cur)
    return depth, int(longest)


def cagr(equity: pd.Series, periods_per_year: int = TRADING_DAYS) -> float:
    eq = pd.to_numeric(pd.Series(equity), errors="coerce").dropna().astype(float)
    eq = eq[eq > 0]
    if len(eq) < 2:
        return 0.0
    years = (len(eq) - 1) / periods_per_year
    if years <= 0:
        return 0.0
    return float((eq.iloc[-1] / eq.iloc[0]) ** (1.0 / years) - 1.0)


def trade_stats(trades: pd.DataFrame | list[dict[str, Any]] | None) -> dict[str, float]:
    """Hit rate / profit factor from realized P&L of closing fills (`realized_pnl` != 0)."""
    if trades is None:
        return {"hit_rate": 0.0, "profit_factor": 0.0, "n_trades": 0, "avg_win": 0.0, "avg_loss": 0.0}
    df = pd.DataFrame(trades) if not isinstance(trades, pd.DataFrame) else trades
    if df.empty or "realized_pnl" not in df.columns:
        return {"hit_rate": 0.0, "profit_factor": 0.0, "n_trades": 0, "avg_win": 0.0, "avg_loss": 0.0}
    pnl = pd.to_numeric(df["realized_pnl"], errors="coerce").dropna()
    pnl = pnl[pnl.abs() > 1e-12]
    if pnl.empty:
        return {"hit_rate": 0.0, "profit_factor": 0.0, "n_trades": 0, "avg_win": 0.0, "avg_loss": 0.0}
    wins, losses = pnl[pnl > 0], pnl[pnl < 0]
    gross_loss = float(-losses.sum())
    return {
        "hit_rate": float(len(wins) / len(pnl)),
        "profit_factor": float(wins.sum() / gross_loss) if gross_loss > 0 else float("inf"),
        "n_trades": len(pnl),
        "avg_win": float(wins.mean()) if not wins.empty else 0.0,
        "avg_loss": float(losses.mean()) if not losses.empty else 0.0,
    }


def summarise(
    equity: pd.Series,
    trades: pd.DataFrame | list[dict[str, Any]] | None = None,
    exposure: pd.Series | None = None,
    turnover_bbl: pd.Series | None = None,
    notional: float | None = None,
) -> PerfSummary:
    eq = pd.to_numeric(pd.Series(equity), errors="coerce").dropna().astype(float)
    rets = returns_from_equity(eq)
    depth, dd_days = max_drawdown(eq)
    growth = cagr(eq)
    ts = trade_stats(trades)
    s = PerfSummary(
        n_obs=len(eq),
        start_equity=float(eq.iloc[0]) if not eq.empty else 0.0,
        end_equity=float(eq.iloc[-1]) if not eq.empty else 0.0,
        total_return=float(eq.iloc[-1] / eq.iloc[0] - 1.0) if len(eq) > 1 and eq.iloc[0] > 0 else 0.0,
        cagr=growth,
        ann_vol=ann_vol(rets),
        sharpe=sharpe(rets),
        sortino=sortino(rets),
        max_drawdown=depth,
        max_drawdown_days=dd_days,
        calmar=float(growth / abs(depth)) if depth < -1e-12 else 0.0,
        hit_rate=ts["hit_rate"],
        profit_factor=ts["profit_factor"],
        n_trades=int(ts["n_trades"]),
        avg_win=ts["avg_win"],
        avg_loss=ts["avg_loss"],
        best_day=float(rets.max()) if not rets.empty else 0.0,
        worst_day=float(rets.min()) if not rets.empty else 0.0,
    )
    if exposure is not None and len(exposure):
        ex = pd.to_numeric(pd.Series(exposure), errors="coerce").dropna()
        s.exposure = float((ex.abs() > 1e-9).mean())
    if turnover_bbl is not None and len(turnover_bbl) and notional:
        s.turnover = float(pd.to_numeric(pd.Series(turnover_bbl), errors="coerce").dropna().sum() / notional)
    if not rets.empty and isinstance(rets.index, pd.DatetimeIndex):
        years = pd.DatetimeIndex(rets.index).year
        for year, grp in rets.groupby(years):
            compounded = float(np.prod(1.0 + grp.to_numpy(dtype=float)))
            s.by_year[str(int(year))] = compounded - 1.0
    return s
