"""Backtest: the shared event loop (`TradingSession`), the day walker (`run_backtest`) and metrics."""

from engine.backtest.metrics import PerfSummary, summarise
from engine.backtest.runner import BacktestResult, run_backtest, trading_days
from engine.backtest.session import DayResult, SessionState, TradingSession

__all__ = [
    "BacktestResult",
    "DayResult",
    "PerfSummary",
    "SessionState",
    "TradingSession",
    "run_backtest",
    "summarise",
    "trading_days",
]
